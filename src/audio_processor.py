"""Audio processing with FFMPEG."""
import logging
import subprocess
import tempfile
import os
import shutil
from pathlib import Path
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

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
        """Check if FFMPEG is available."""
        try:
            subprocess.run(['ffmpeg', '-version'],
                         capture_output=True, check=True, timeout=5)
            return True
        except (subprocess.SubprocessError, FileNotFoundError):
            logger.error("FFMPEG not found or not working")
            return False

    def get_audio_duration(self, audio_path: str) -> Optional[float]:
        """Get duration of audio file in seconds."""
        try:
            cmd = [
                'ffprobe', '-v', 'error',
                '-show_entries', 'format=duration',
                '-of', 'default=noprint_wrappers=1:nokey=1',
                audio_path
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if result.returncode == 0:
                return float(result.stdout.strip())
        except Exception as e:
            logger.error(f"Failed to get audio duration: {e}")
        return None

    def get_beep_duration(self) -> float:
        """Get duration of beep audio (cached)."""
        if self._beep_duration is None:
            self._beep_duration = self.get_audio_duration(self.replace_audio_path) or 1.0
        return self._beep_duration

    def remove_ads(self, input_path: str, ad_segments: List[Dict], output_path: str) -> bool:
        """Remove ad segments from audio file."""
        if not ad_segments:
            # No ads to remove, just copy file
            logger.info("No ads to remove, copying original file")
            shutil.copy2(input_path, output_path)
            return True

        if not os.path.exists(self.replace_audio_path):
            logger.error(f"Replace audio not found: {self.replace_audio_path}")
            return False

        try:
            # Get total duration
            total_duration = self.get_audio_duration(input_path)
            if not total_duration:
                logger.error("Could not get audio duration")
                return False

            logger.info(f"Processing audio: {total_duration:.1f}s total, {len(ad_segments)} ad segments")

            # Sort ad segments by start time
            sorted_segments = sorted(ad_segments, key=lambda x: x['start'])

            # Merge segments with < 1 second gaps
            merged_ads = []
            current_segment = None

            for ad in sorted_segments:
                if current_segment and ad['start'] - current_segment['end'] < 1.0:
                    # Extend current segment (use max to handle overlapping/contained ads)
                    current_segment['end'] = max(current_segment['end'], ad['end'])
                    if 'reason' in ad:
                        current_segment['reason'] = current_segment.get('reason', '') + '; ' + ad['reason']
                else:
                    if current_segment:
                        merged_ads.append(current_segment)
                    current_segment = {'start': ad['start'], 'end': ad['end']}
                    if 'reason' in ad:
                        current_segment['reason'] = ad['reason']

            if current_segment:
                merged_ads.append(current_segment)

            # Filter out short ad detections (< 10 seconds) - likely false positives
            MIN_AD_DURATION_FOR_REMOVAL = 10.0  # seconds
            ads = []
            skipped_count = 0
            for ad in merged_ads:
                duration = ad['end'] - ad['start']
                if duration >= MIN_AD_DURATION_FOR_REMOVAL:
                    ads.append(ad)
                else:
                    skipped_count += 1
                    logger.info(f"Skipping short ad ({duration:.1f}s < {MIN_AD_DURATION_FOR_REMOVAL}s): {ad.get('reason', 'unknown')[:50]}")

            if skipped_count > 0:
                logger.info(f"Skipped {skipped_count} short ad detections (< {MIN_AD_DURATION_FOR_REMOVAL}s)")
            logger.info(f"After merging and filtering: {len(ads)} ad segments")

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

            # Threshold for trimming end-of-episode ads (beep then cut)
            POST_ROLL_TRIM_THRESHOLD = 30.0  # seconds

            for i, ad in enumerate(ads):
                ad_start = ad['start']
                ad_end = ad['end']
                is_last_ad = (i == num_ads - 1)

                # Check if this is an end-of-episode ad that should be trimmed after beep
                # (last ad with < 30s of content remaining after it)
                remaining_after_ad = total_duration - ad_end
                if is_last_ad and remaining_after_ad < POST_ROLL_TRIM_THRESHOLD:
                    # End-of-episode ad: add content, beep, then end (no trailing content)
                    logger.info(f"End-of-episode ad at {ad_start:.1f}s - will add beep then end (only {remaining_after_ad:.1f}s would remain)")
                    if ad_start > current_time:
                        content_duration = ad_start - current_time
                        # Add final content with fade out at end
                        if i == 0:
                            if content_duration > fade_out_duration:
                                filter_parts.append(f"[0:a]atrim={current_time}:{ad_start},asetpts=PTS-STARTPTS,afade=t=out:st={content_duration - fade_out_duration}:d={fade_out_duration}[s{segment_idx}]")
                            else:
                                filter_parts.append(f"[0:a]atrim={current_time}:{ad_start},asetpts=PTS-STARTPTS[s{segment_idx}]")
                        else:
                            if content_duration > fade_in_duration + fade_out_duration:
                                filter_parts.append(f"[0:a]atrim={current_time}:{ad_start},asetpts=PTS-STARTPTS,afade=t=in:d={fade_in_duration},afade=t=out:st={content_duration - fade_out_duration}:d={fade_out_duration}[s{segment_idx}]")
                            else:
                                filter_parts.append(f"[0:a]atrim={current_time}:{ad_start},asetpts=PTS-STARTPTS[s{segment_idx}]")
                        concat_parts.append(f"[s{segment_idx}]")
                        segment_idx += 1

                    # Add beep before ending episode
                    beep_fade_out_start = max(0, beep_duration - beep_fade_duration)
                    beep_input = f"[beep_in{i}]" if num_ads > 1 else "[1:a]"
                    filter_parts.append(f"{beep_input}afade=t=in:d={beep_fade_duration},afade=t=out:st={beep_fade_out_start}:d={beep_fade_duration},volume=0.4[beep{segment_idx}]")
                    concat_parts.append(f"[beep{segment_idx}]")
                    # Episode ends here - don't process remaining content
                    break

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
            else:
                # Only add remaining content if we didn't break (trim end-of-episode ad)
                # Add remaining content after last ad (with fade-in)
                # Skip if less than 30 seconds remain (post-roll ad residue)
                if current_time < total_duration:
                    content_duration = total_duration - current_time
                    if content_duration < POST_ROLL_TRIM_THRESHOLD:
                        logger.info(f"Skipping {content_duration:.1f}s of post-roll content (< {POST_ROLL_TRIM_THRESHOLD}s threshold)")
                    elif content_duration > fade_in_duration:
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
            # Use capture_output without text=True to get raw bytes
            # FFMPEG can output non-UTF-8 characters (progress bars, special chars)
            # which would cause UnicodeDecodeError if we used text=True
            result = subprocess.run(cmd, capture_output=True, timeout=300)

            if result.returncode != 0:
                # Safely decode stderr, replacing any non-UTF-8 characters
                try:
                    stderr_text = result.stderr.decode('utf-8', errors='replace')
                except Exception:
                    stderr_text = str(result.stderr)[:500]
                logger.error(f"FFMPEG failed: {stderr_text}")
                return False

            # Verify output
            new_duration = self.get_audio_duration(output_path)
            if new_duration:
                removed_time = total_duration - new_duration
                logger.info(f"FFMPEG processing complete: {total_duration:.1f}s → {new_duration:.1f}s (removed {removed_time:.1f}s)")
                return True
            else:
                logger.error("Could not verify output file")
                return False

        except subprocess.TimeoutExpired:
            logger.error("FFMPEG processing timed out")
            return False
        except Exception as e:
            logger.error(f"Audio processing failed: {e}")
            return False

    def process_episode(self, input_path: str, ad_segments: List[Dict]) -> Optional[str]:
        """Process episode audio to remove ads."""
        with tempfile.NamedTemporaryFile(delete=False, suffix='.mp3') as tmp:
            temp_output = tmp.name

        try:
            if self.remove_ads(input_path, ad_segments, temp_output):
                return temp_output
            else:
                # Clean up on failure
                if os.path.exists(temp_output):
                    os.unlink(temp_output)
                return None
        except Exception as e:
            logger.error(f"Episode processing failed: {e}")
            if os.path.exists(temp_output):
                os.unlink(temp_output)
            return None