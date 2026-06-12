"""Audio processing with FFMPEG."""
import logging
import subprocess
import tempfile
import os
import shutil
from functools import lru_cache
from pathlib import Path
from typing import List, Dict, Optional, Tuple

from utils.audio import get_audio_duration
from utils.subprocess_registry import tracked_run
from config import (
    FFMPEG_LONG_TIMEOUT, SUBPROCESS_VERSION_PROBE,
    MIN_AD_DURATION_FOR_REMOVAL, POST_ROLL_TRIM_THRESHOLD, MERGE_GAP_SECONDS,
    SHORT_CUT_KEEP_CONFIDENCE,
)

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _check_ffmpeg_once() -> bool:
    """Verify ffmpeg is on PATH. Cached so at most one subprocess fork runs
    per worker lifetime regardless of how many AudioProcessor instances the
    caller spins up."""
    try:
        subprocess.run(
            ['ffmpeg', '-version'],
            capture_output=True, check=True, timeout=SUBPROCESS_VERSION_PROBE,
        )
        return True
    except (subprocess.SubprocessError, FileNotFoundError):
        logger.error("FFMPEG not found or not working")
        return False

# Get the assets directory - check primary location first, fall back to builtin
ASSETS_DIR = Path(__file__).parent.parent / "assets"
ASSETS_BUILTIN_DIR = Path(__file__).parent.parent / "assets_builtin"


def get_replace_audio_path() -> str:
    """Get the path to replace.mp3, checking primary assets first, then builtin."""
    primary_path = ASSETS_DIR / "replace.mp3"
    builtin_path = ASSETS_BUILTIN_DIR / "replace.mp3"

    if primary_path.exists():
        return str(primary_path)
    elif builtin_path.exists():
        return str(builtin_path)
    else:
        # Return primary path anyway (will fail later with clear error)
        return str(primary_path)


DEFAULT_REPLACE_AUDIO = get_replace_audio_path()


class AudioProcessor:
    def __init__(self, replace_audio_path: str = None, bitrate: str = '128k'):
        self.replace_audio_path = replace_audio_path or DEFAULT_REPLACE_AUDIO
        self.bitrate = bitrate
        self._beep_duration = None  # Cached beep duration

    def check_ffmpeg(self) -> bool:
        """Check if FFMPEG is available. Result is cached per process so the
        subprocess fork only runs once per worker lifetime."""
        return _check_ffmpeg_once()

    def get_audio_duration(self, audio_path: str) -> Optional[float]:
        """Get duration of audio file in seconds.

        Delegates to utils.audio.get_audio_duration for consistent implementation.
        """
        return get_audio_duration(audio_path)

    def get_beep_duration(self) -> float:
        """Get duration of beep audio (cached)."""
        if self._beep_duration is None:
            self._beep_duration = self.get_audio_duration(self.replace_audio_path) or 1.0
        return self._beep_duration

    def compute_applied_cuts(self, ad_segments: List[Dict],
                             total_duration: float) -> List[Dict]:
        """Compute the cuts remove_ads actually applies to the audio.

        Requested segments diverge from applied cuts: near-adjacent segments
        merge, short ones drop, and an end-of-episode cut extends to the end
        of the file. Asset generation and verification timestamp mapping need
        the applied list, not the requested one -- remove_ads returns it.
        """
        if not ad_segments or not total_duration:
            return []

        # Clamp to the audio bounds: detection can hand us a cut that starts
        # below zero or runs past the end of the file, and an out-of-range
        # atrim would silently cut the wrong region.
        clamped = []
        for ad in ad_segments:
            start = max(0.0, ad['start'])
            end = min(ad['end'], total_duration)
            if end <= start:
                logger.info(f"Skipping out-of-range ad ({ad['start']:.1f}s-{ad['end']:.1f}s "
                            f"vs {total_duration:.1f}s audio)")
                continue
            ad = dict(ad)
            ad['start'], ad['end'] = start, end
            clamped.append(ad)
        if not clamped:
            return []

        sorted_segments = sorted(clamped, key=lambda x: x['start'])

        # Merge segments with < 1 second gaps
        merged_ads = []
        current_segment = None
        for ad in sorted_segments:
            if current_segment and ad['start'] - current_segment['end'] < MERGE_GAP_SECONDS:
                # Extend current segment (use max to handle overlapping/contained ads)
                current_segment['end'] = max(current_segment['end'], ad['end'])
                if 'reason' in ad:
                    current_segment['reason'] = current_segment.get('reason', '') + '; ' + ad['reason']
                # Carry the strongest trust signal of the merged members so
                # the short-cut filter below can judge the merged span.
                if ad.get('confidence', 0) > current_segment.get('confidence', 0):
                    current_segment['confidence'] = ad['confidence']
                if ad.get('detection_stage') == 'fingerprint':
                    current_segment['detection_stage'] = 'fingerprint'
            else:
                if current_segment:
                    merged_ads.append(current_segment)
                current_segment = {'start': ad['start'], 'end': ad['end']}
                for key in ('reason', 'confidence', 'detection_stage'):
                    if key in ad:
                        current_segment[key] = ad[key]
        if current_segment:
            merged_ads.append(current_segment)

        # Filter out short ad detections (< 10 seconds) - likely LLM
        # hallucinations. Trusted short cuts stay: a fingerprint match is a
        # known ad pattern, and a high-confidence detection (e.g. an 8s
        # bumper) is more likely real than noise.
        ads = []
        skipped_count = 0
        for ad in merged_ads:
            duration = ad['end'] - ad['start']
            trusted = (ad.get('detection_stage') == 'fingerprint'
                       or ad.get('confidence', 0) >= SHORT_CUT_KEEP_CONFIDENCE)
            if duration >= MIN_AD_DURATION_FOR_REMOVAL:
                ads.append(ad)
            elif trusted:
                ads.append(ad)
                logger.info(
                    f"Keeping short ad ({duration:.1f}s < {MIN_AD_DURATION_FOR_REMOVAL}s): "
                    f"stage={ad.get('detection_stage', '?')} "
                    f"confidence={ad.get('confidence', 'n/a')}")
            else:
                skipped_count += 1
                logger.info(f"Skipping short ad ({duration:.1f}s < {MIN_AD_DURATION_FOR_REMOVAL}s): {ad.get('reason', 'unknown')[:50]}")
        if skipped_count > 0:
            logger.info(f"Skipped {skipped_count} short ad detections (< {MIN_AD_DURATION_FOR_REMOVAL}s)")

        # End-of-episode cut: when less than 30s would remain after the last
        # cut, the episode ends at the beep, so the cut runs to the end.
        if ads:
            remaining = total_duration - ads[-1]['end']
            if remaining < POST_ROLL_TRIM_THRESHOLD and ads[-1]['end'] != total_duration:
                logger.info(f"End-of-episode cut: extending {ads[-1]['end']:.1f}s -> "
                            f"{total_duration:.1f}s ({remaining:.1f}s would remain)")
                ads[-1]['end'] = total_duration

        return ads

    def remove_ads(self, input_path: str, ad_segments: List[Dict],
                   output_path: str) -> Optional[List[Dict]]:
        """Remove ad segments from audio file.

        Returns the applied cut list (see compute_applied_cuts) on success --
        empty when nothing was cut -- or None on failure.
        """
        if not ad_segments:
            # No ads to remove, just copy file
            logger.info("No ads to remove, copying original file")
            shutil.copy2(input_path, output_path)
            return []

        if not os.path.exists(self.replace_audio_path):
            logger.error(f"Replace audio not found: {self.replace_audio_path}")
            return None

        try:
            # Get total duration
            total_duration = self.get_audio_duration(input_path)
            if not total_duration:
                logger.error("Could not get audio duration")
                return None

            logger.info(f"Processing audio: {total_duration:.1f}s total, {len(ad_segments)} ad segments")

            ads = self.compute_applied_cuts(ad_segments, total_duration)
            logger.info(f"After merging and filtering: {len(ads)} ad segments")
            if not ads:
                # Every requested cut merged/filtered away: nothing to cut,
                # so skip the re-encode and ship the audio unchanged.
                shutil.copy2(input_path, output_path)
                return []

            # Build complex filter for FFMPEG
            # Strategy: Split audio into segments, replace ad segments with beep
            filter_parts = []
            concat_parts = []
            current_time = 0
            segment_idx = 0

            # Fade durations in seconds for smooth ad transitions
            fade_out_duration = 0.5  # Content fade-out before beep
            fade_in_duration = 0.8   # Content fade-in after beep (longer ease back)
            beep_fade_duration = 0.5  # Beep fades stay short
            beep_duration = self.get_beep_duration()

            # Split beep input into N copies (one per ad) - ffmpeg streams can only be used once
            num_ads = len(ads)
            if num_ads > 1:
                beep_split = f"[1:a]asplit={num_ads}" + "".join(f"[beep_in{i}]" for i in range(num_ads))
                filter_parts.append(beep_split)

            for i, ad in enumerate(ads):
                ad_start = ad['start']
                ad_end = ad['end']

                # Add content before ad (with fades at boundaries)
                if ad_start > current_time:
                    content_duration = ad_start - current_time
                    # First segment: only fade-out at end
                    # Subsequent segments: fade-in at start, fade-out at end
                    if i == 0:
                        # First content segment - just fade out before ad
                        if content_duration > fade_out_duration:
                            filter_parts.append(f"[0:a]atrim={current_time}:{ad_start},asetpts=PTS-STARTPTS,afade=t=out:st={content_duration - fade_out_duration}:d={fade_out_duration}[s{segment_idx}]")
                        else:
                            filter_parts.append(f"[0:a]atrim={current_time}:{ad_start},asetpts=PTS-STARTPTS[s{segment_idx}]")
                    else:
                        # Content between ads - fade in at start, fade out at end
                        if content_duration > fade_in_duration + fade_out_duration:
                            filter_parts.append(f"[0:a]atrim={current_time}:{ad_start},asetpts=PTS-STARTPTS,afade=t=in:d={fade_in_duration},afade=t=out:st={content_duration - fade_out_duration}:d={fade_out_duration}[s{segment_idx}]")
                        else:
                            filter_parts.append(f"[0:a]atrim={current_time}:{ad_start},asetpts=PTS-STARTPTS[s{segment_idx}]")
                    concat_parts.append(f"[s{segment_idx}]")
                    segment_idx += 1

                # Add single replacement audio with fades and volume reduction to 40%
                # Calculate fade-out start time (beep_duration - beep_fade_duration, minimum 0)
                beep_fade_out_start = max(0, beep_duration - beep_fade_duration)
                # Use split copy if multiple ads, otherwise use original input
                beep_input = f"[beep_in{i}]" if num_ads > 1 else "[1:a]"
                filter_parts.append(f"{beep_input}afade=t=in:d={beep_fade_duration},afade=t=out:st={beep_fade_out_start}:d={beep_fade_duration},volume=0.4[beep{segment_idx}]")
                concat_parts.append(f"[beep{segment_idx}]")

                current_time = ad_end

            # Add remaining content after the last ad (with fade-in).
            # compute_applied_cuts extends an end-of-episode cut to
            # total_duration, so the episode ends at the beep in that case
            # and no short post-roll residue can reach here.
            if current_time < total_duration:
                content_duration = total_duration - current_time
                if content_duration > fade_in_duration:
                    filter_parts.append(f"[0:a]atrim={current_time}:{total_duration},asetpts=PTS-STARTPTS,afade=t=in:d={fade_in_duration}[s{segment_idx}]")
                    concat_parts.append(f"[s{segment_idx}]")
                else:
                    filter_parts.append(f"[0:a]atrim={current_time}:{total_duration},asetpts=PTS-STARTPTS[s{segment_idx}]")
                    concat_parts.append(f"[s{segment_idx}]")

            # Concatenate all parts
            filter_str = ';'.join(filter_parts)
            if filter_str:
                filter_str += ';'
            filter_str += ''.join(concat_parts) + f"concat=n={len(concat_parts)}:v=0:a=1[out]"

            # Run FFMPEG
            cmd = [
                'ffmpeg', '-y',
                '-i', input_path,
                '-i', self.replace_audio_path,
                '-filter_complex', filter_str,
                '-map', '[out]',
                '-acodec', 'libmp3lame',
                '-ab', self.bitrate,
                output_path
            ]

            logger.info(f"Running FFMPEG to remove ads")
            # Scale timeout: 5 min base + 5 sec per minute of audio
            # e.g. 30-min episode = 450s, 107-min = 835s, 180-min = 1200s
            ffmpeg_timeout = FFMPEG_LONG_TIMEOUT + int(total_duration / 12)
            logger.debug(f"FFMPEG timeout: {ffmpeg_timeout}s for {total_duration:.0f}s audio")
            # Use capture_output without text=True to get raw bytes
            # FFMPEG can output non-UTF-8 characters (progress bars, special chars)
            # which would cause UnicodeDecodeError if we used text=True
            result = tracked_run(cmd, capture_output=True, timeout=ffmpeg_timeout)

            if result.returncode != 0:
                # Safely decode stderr, replacing any non-UTF-8 characters
                try:
                    stderr_text = result.stderr.decode('utf-8', errors='replace')
                except Exception:
                    stderr_text = str(result.stderr)[:500]
                logger.error(f"FFMPEG failed: {stderr_text}")
                return None

            # Verify output
            new_duration = self.get_audio_duration(output_path)
            if new_duration:
                removed_time = total_duration - new_duration
                logger.info(f"FFMPEG processing complete: {total_duration:.1f}s → {new_duration:.1f}s (removed {removed_time:.1f}s)")
                return ads
            else:
                logger.error("Could not verify output file")
                return None

        except subprocess.TimeoutExpired:
            logger.error(f"FFMPEG processing timed out after {ffmpeg_timeout}s")
            return None
        except Exception as e:
            logger.error(f"Audio processing failed: {e}")
            return None

    def process_episode(self, input_path: str,
                        ad_segments: List[Dict]) -> Optional[Tuple[str, List[Dict]]]:
        """Process episode audio to remove ads.

        Returns (output_path, applied_cuts) on success, None on failure.
        applied_cuts is the merged/filtered/end-trimmed list remove_ads cut,
        which downstream asset generation and timestamp mapping consume.
        """
        with tempfile.NamedTemporaryFile(delete=False, suffix='.mp3') as tmp:
            temp_output = tmp.name

        try:
            applied_cuts = self.remove_ads(input_path, ad_segments, temp_output)
            if applied_cuts is not None:
                return temp_output, applied_cuts
            # Clean up on failure
            if os.path.exists(temp_output):
                os.unlink(temp_output)
            return None
        except Exception as e:
            logger.error(f"Episode processing failed: {e}")
            if os.path.exists(temp_output):
                os.unlink(temp_output)
            return None