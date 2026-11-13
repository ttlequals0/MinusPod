"""Verification pass: re-run detection on the post-cut transcript to catch missed ads.

Normally re-transcribes the processed audio; LLM-only reprocess maps the
saved transcript through the cuts instead (#349). Returns dual timestamps:
original-audio for UI/DB, processed-audio for cutting.
"""

import logging
from copy import deepcopy

from audio_processor import get_replacement_duration
from config import UNREVIEWABLE_GAP_SECONDS
from transcript_generator import TranscriptGenerator
from utils.errors import ServiceUnavailableError, AudioExtractionError
from utils.language import get_feed_language_override
from utils.time import adjust_timestamp

logger = logging.getLogger('podcast.verification')


class VerificationPass:
    """Runs detection on processed audio to find missed ads.

    Steps: transcribe the pass 1 output, analyze audio, run detection with the
    verification prompt, then map processed timestamps back to original.
    """

    def __init__(self, ad_detector, transcriber, audio_analyzer,
                 pattern_service=None, db=None):
        self.ad_detector = ad_detector
        self.transcriber = transcriber
        self.audio_analyzer = audio_analyzer
        self.pattern_service = pattern_service
        self.db = db

    def verify(self, processed_audio_path: str, podcast_name: str,
               episode_title: str, slug: str, episode_id: str,
               pass1_cuts: list[dict] = None,
               episode_description: str = None,
               podcast_description: str = None,
               detection_notes: str = None,
               skip_patterns: bool = False,
               progress_callback=None,
               original_segments: list[dict] = None,
               reuse_transcript: bool = False,
               feed_id: int | None = None,
               action_map: dict[str, str] | None = None) -> dict:
        """Run detection on processed audio to find missed ads.

        pass1_cuts: ad dicts removed in pass 1 (start/end), used to map back to
        original audio. action_map: the run's category actions, passed to detection.

        Returns a dict: 'ads' (original-audio timestamps, for UI/DB),
        'ads_processed' (processed-audio timestamps, for cutting), 'segments',
        and 'status' ('clean', 'found_ads', 'no_segments',
        'transcription_failed' or 'detection_failed').
        """
        # Step 1: Get verification segments.
        if not pass1_cuts and original_segments:
            # Pass 1 cut nothing, so the processed audio is identical to the
            # original; reuse the transcript instead of re-transcribing (#349).
            logger.info(
                f"[{slug}:{episode_id}] Verification: reusing original transcript "
                f"({len(original_segments)} segments, no pass 1 cuts)"
            )
            # Shallow copy so this matches the re-transcribe path (a fresh list)
            # and downstream can't mutate the caller's segment list in place.
            verification_segments = list(original_segments)
        elif reuse_transcript and pass1_cuts and original_segments:
            # LLM-only reprocess (#349): map the saved transcript through the cuts onto the
            # beeped timeline instead of re-transcribing; pass 2 only sees what it captured.
            verification_segments = TranscriptGenerator().compute_final_segments(
                original_segments, pass1_cuts, get_replacement_duration()
            )
            logger.info(
                f"[{slug}:{episode_id}] Verification: mapped saved transcript "
                f"through {len(pass1_cuts)} cuts -> {len(verification_segments)} "
                f"segments (no re-transcription, issue #349)"
            )
        else:
            if progress_callback:
                progress_callback("transcribing", 85)
            logger.info(f"[{slug}:{episode_id}] Verification: Re-transcribing processed audio")
            try:
                verification_segments = self._transcribe_verification(processed_audio_path, slug=slug)
            except (ServiceUnavailableError, AudioExtractionError) as e:
                # Best-effort: a Whisper outage or chunk extraction failure (#556) must not
                # fail an already-cut episode; the failed status still persists the pass-2 record (#482).
                logger.warning(f"[{slug}:{episode_id}] Verification: transcription unavailable, skipping pass 2: {e}")
                return {'ads': [], 'ads_processed': [], 'segments': [],
                        'status': 'transcription_failed'}

        if not verification_segments:
            logger.warning(f"[{slug}:{episode_id}] Verification: No segments")
            return {'ads': [], 'ads_processed': [], 'segments': [], 'status': 'no_segments'}

        logger.info(f"[{slug}:{episode_id}] Verification: {len(verification_segments)} segments")

        # Step 2: Audio analysis on processed audio
        if progress_callback:
            progress_callback("analyzing", 88)
        processed_analysis = None
        try:
            # The feed PK lets the cue analyzer pick the per-feed template matcher
            # instead of only the spectral fallback.
            processed_analysis = self.audio_analyzer.analyze(
                processed_audio_path, feed_id=feed_id,
            )
            if processed_analysis and processed_analysis.signals:
                logger.info(f"[{slug}:{episode_id}] Verification: "
                           f"{len(processed_analysis.signals)} audio signals")
        except Exception as e:
            logger.warning(f"[{slug}:{episode_id}] Verification audio analysis failed: {e}")

        # Audio cues (issue #350) found on the processed audio. The stat counter
        # in processing only sees the pass-1 analysis, so surface this count so
        # cues found here are not dropped from the dashboard total.
        verification_cue_count = (
            len(processed_analysis.get_signals_by_type('audio_cue'))
            if processed_analysis else 0
        )

        # Step 3: Claude detection with verification prompt + audio context
        if progress_callback:
            progress_callback("detecting", 90)
        verification_result = self.ad_detector.run_verification_detection(
            verification_segments, podcast_name, episode_title,
            slug, episode_id, episode_description,
            podcast_description=podcast_description,
            detection_notes=detection_notes,
            progress_callback=progress_callback,
            audio_analysis=processed_analysis,
            pass1_cuts=pass1_cuts,
            action_map=action_map,
        )
        # Window counts ride along on every post-detection return so the run
        # stats can report coverage the verification scan never examined.
        window_counts = {
            'windows_total': verification_result.get('windows_total'),
            'windows_failed': verification_result.get('windows_failed'),
            'windows_failure_classes': verification_result.get(
                'windows_failure_classes'),
        }
        detection_error = verification_result.get('error')
        if verification_result.get('status') == 'failed' or detection_error:
            # Enough detection windows failed that an unknown share of the
            # audio went unexamined, so this pass is not a clean scan.
            logger.warning(
                f"[{slug}:{episode_id}] Verification detection failed: {detection_error}")
            return {'ads': [], 'ads_processed': [], 'segments': verification_segments,
                    'status': 'detection_failed', 'error': detection_error,
                    'rate_limited_hold': verification_result.get('rate_limited_hold', False),
                    'retry_after_seconds': verification_result.get('retry_after_seconds'),
                    'provider_key': verification_result.get('provider_key'),
                    'credential_slot': verification_result.get('credential_slot', 'primary'),
                    'manual': verification_result.get('manual', False),
                    'audio_cue_count': verification_cue_count, **window_counts}

        processed_ads = verification_result.get('ads', [])

        if not processed_ads:
            return {'ads': [], 'ads_processed': [], 'segments': verification_segments,
                    'status': 'clean', 'audio_cue_count': verification_cue_count,
                    **window_counts}

        # Tag all ads as verification stage
        for ad in processed_ads:
            ad['detection_stage'] = 'verification'

        # Step 4: Map processed timestamps back to original-audio timestamps
        original_ads = []
        if pass1_cuts:
            timestamp_map = _build_timestamp_map(pass1_cuts)
            beep = get_replacement_duration()
            original_ads = [
                _map_ad_to_original(ad, timestamp_map, beep)
                for ad in processed_ads
            ]
            logger.info(f"[{slug}:{episode_id}] Verification: mapped {len(original_ads)} ads "
                       f"to original timestamps using {len(pass1_cuts)} pass 1 cuts")
        else:
            # No pass 1 cuts means no timestamp shift: processed equals original
            original_ads = deepcopy(processed_ads)
            logger.info(f"[{slug}:{episode_id}] Verification: no pass 1 cuts, "
                       f"timestamps are already original")

        logger.info(f"[{slug}:{episode_id}] Verification found {len(processed_ads)} missed ads")
        for ad in original_ads:
            logger.info(
                f"[{slug}:{episode_id}] Verification false negative: "
                f"{ad.get('sponsor', 'unknown')} "
                f"{ad['start']:.1f}-{ad['end']:.1f}s "
                f"confidence={ad.get('confidence', 'N/A')}"
            )

        # Match first-pass learning: a kept marker still represents a real ad
        # read and remains eligible for pattern learning.
        if self.pattern_service and original_ads:
            try:
                self.pattern_service.record_verification_misses(
                    slug, episode_id, original_ads, segments=original_segments
                )
            except Exception as e:
                logger.warning(f"[{slug}:{episode_id}] Failed to record verification misses: {e}")

        return {
            'ads': original_ads,
            'ads_processed': processed_ads,
            'segments': verification_segments,
            'status': 'found_ads',
            'audio_cue_count': verification_cue_count,
            **window_counts,
        }

    def _transcribe_verification(self, audio_path: str,
                                 slug: str = None) -> list[dict]:
        """Re-transcribe via transcribe_chunked() so audio over the backend's
        single-request limit (e.g. OpenAI whisper's 25MB) is chunked, not a 413.

        Exceptions propagate so the status distinguishes 'transcription_failed'
        from 'no_segments'.
        """
        language_override = get_feed_language_override(self.db, slug)
        segments = self.transcriber.transcribe_chunked(
            audio_path, language_override=language_override,
        )
        if not segments:
            return segments
        # Same hole repair as pass 1, without the per-episode memo.
        added, _empty = self.transcriber.repair_gaps(
            audio_path, segments, UNREVIEWABLE_GAP_SECONDS, language_override)
        return sorted(segments + added, key=lambda seg: seg['start']) if added else segments


def _build_timestamp_map(pass1_cuts: list[dict]) -> list[tuple[float, float, float | None]]:
    """Build a sorted list of (cut_start, cut_duration, cut_replacement) from pass 1 removed ads.

    cut_replacement is the span's own 'replacement_duration', else None;
    callers fall back to their `replacement_duration` argument for legacy
    rows. Used by _map_to_original to reverse the timestamp shift.
    """
    cuts = []
    for ad in pass1_cuts:
        start = ad.get('start', 0)
        end = ad.get('end', 0)
        duration = end - start
        if duration > 0:
            cuts.append((start, duration, ad.get('replacement_duration')))
    cuts.sort(key=lambda x: x[0])
    return cuts


_AD_TIMESTAMP_FIELDS = (
    'start', 'end', 'text_start', 'text_end',
    'merged_protected_start', 'merged_protected_end',
    'fingerprint_match_start', 'fingerprint_match_end',
)
_AD_SPAN_FIELDS = ('merged_member_spans', 'dai_core_spans', 'dai_probe_spans')


def _map_ad_to_original(ad: dict, timestamp_map: list[tuple],
                        replacement_duration: float) -> dict:
    """Deep-copy an ad and map its processed-audio spans."""
    mapped = deepcopy(ad)

    def map_field(container, key):
        value = container.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            container[key] = _map_to_original(
                float(value), timestamp_map, replacement_duration)

    for field in _AD_TIMESTAMP_FIELDS:
        map_field(mapped, field)
    for field in _AD_SPAN_FIELDS:
        spans = mapped.get(field)
        if not isinstance(spans, list):
            continue
        for span in spans:
            if isinstance(span, dict):
                for key in ('start', 'end', 'fingerprint_match_start',
                            'fingerprint_match_end'):
                    map_field(span, key)
    return mapped


def _cut_replacement(cut: tuple, default: float | None = None) -> float | None:
    """A cut tuple's own replacement duration (third element), or `default`
    for a 2-tuple or an explicit None third element."""
    return cut[2] if len(cut) > 2 and cut[2] is not None else default


def _map_to_original(processed_time: float,
                     cuts: list[tuple[float, float]],
                     replacement_duration: float = 0.0) -> float:
    """Map a processed-audio timestamp back to original-audio time.

    Each cut shifts later content by (duration - replacement). A timestamp
    inside a replacement maps just inside the removed span it stands in for,
    keeping the mapping monotonic (the inverse of utils.time.adjust_timestamp).
    """
    offset = 0.0
    for cut in cuts:
        cut_start, cut_duration = cut[0], cut[1]
        cut_replacement = _cut_replacement(cut, replacement_duration)
        # In the processed timeline this cut's replacement starts at cut_start - offset.
        replacement_start = cut_start - offset
        if processed_time >= replacement_start + cut_replacement:
            offset += cut_duration - cut_replacement
        elif processed_time > replacement_start:
            return cut_start + (processed_time - replacement_start)
        else:
            break
    return processed_time + offset


def _map_correction_to_processed(
    orig_start: float,
    orig_end: float,
    cuts: list[tuple[float, float]],
    replacement_duration: float = 0.0,
) -> tuple[float, float] | None:
    """Map a user-flagged range [orig_start, orig_end) to processed-audio time.

    Returns the portion that survives pass 1's `cuts` (sorted by start; 2- or
    3-tuples), or None when a cut removed the whole range. An endpoint inside
    a cut snaps to the cut's edge, then both shift left by the cut duration
    ending before them.
    """
    if orig_end <= orig_start:
        return None

    start, end = orig_start, orig_end
    for cut in cuts:
        cut_start, cut_duration = cut[0], cut[1]
        cut_end = cut_start + cut_duration
        if cut_start <= start < cut_end:
            start = cut_end
        if cut_start < end <= cut_end:
            end = cut_start

    if end <= start:
        return None

    # Endpoints are never strictly inside a cut now, so adjust_timestamp does
    # the shift (one owner for the duration-minus-replacement model).
    span_dicts = []
    for cut in cuts:
        s, d = cut[0], cut[1]
        entry = {'start': s, 'end': s + d}
        replacement = _cut_replacement(cut)
        if replacement is not None:
            entry['replacement_duration'] = replacement
        span_dicts.append(entry)
    proc_start = adjust_timestamp(start, span_dicts, replacement_duration)
    proc_end = adjust_timestamp(end, span_dicts, replacement_duration)
    if proc_end <= proc_start:
        return None
    return (proc_start, proc_end)
