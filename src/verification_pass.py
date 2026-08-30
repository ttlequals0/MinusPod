"""
Verification pass for ad detection.

After the first pass detects and removes ads, this module gets the processed
transcript and runs detection again with a "what doesn't belong" prompt to
catch missed ads. It normally re-transcribes the processed audio, but in
LLM-only reprocess it maps the saved transcript through the cuts instead, so
iterating on detection needs no transcription (issue #349). Returns dual
timestamps: original-audio coordinates for UI/DB and processed-audio
coordinates for cutting.
"""

import logging

from audio_processor import get_replacement_duration
from transcript_generator import TranscriptGenerator
from utils.errors import ServiceUnavailableError, AudioExtractionError
from utils.language import get_feed_language_override
from utils.time import adjust_timestamp

logger = logging.getLogger('podcast.verification')


class VerificationPass:
    """
    Runs the full detection pipeline on processed audio to find missed ads.

    The verification pass:
    1. Re-transcribes the pass 1 output on GPU (singleton lazy-reloads)
    2. Runs audio analysis (volume + transitions)
    3. Runs Claude detection with verification prompt + audio context
    4. Maps processed-audio timestamps back to original-audio timestamps
    5. Returns both coordinate sets (original for UI, processed for cutting)
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
               skip_patterns: bool = False,
               progress_callback=None,
               original_segments: list[dict] = None,
               reuse_transcript: bool = False,
               feed_id: int | None = None) -> dict:
        """
        Run full pipeline on processed audio to find missed ads.

        Args:
            pass1_cuts: List of ad dicts removed in pass 1 (need start/end).
                        Used to build the timestamp map back to original audio.

        Returns dict with:
            'ads': list of ad dicts in ORIGINAL-audio timestamps (for UI/DB)
            'ads_processed': list of ad dicts in PROCESSED-audio timestamps (for cutting)
            'segments': transcript segments from verification
            'status': 'clean', 'found_ads', 'no_segments',
                      'transcription_failed', or 'detection_failed'
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
            # LLM-only reprocess (#349): map the saved transcript through the
            # applied cuts instead of re-transcribing the processed audio. The
            # surviving audio is unchanged, so a re-transcription would yield the
            # same words; only the segment boundaries differ. This is what makes
            # LLM-only iteration transcription-free. The trade is that pass 2 can
            # only re-examine what the saved transcript already captured -- it
            # cannot surface an ad whose audio the transcript missed entirely.
            # Map onto the beeped timeline: the processed audio this pass
            # re-examines has a replacement beep where each cut was, exactly
            # like a re-transcription would see it.
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
                verification_segments = self._transcribe_verification(processed_audio_path, podcast_name, slug=slug)
            except (ServiceUnavailableError, AudioExtractionError) as e:
                # Verification is best-effort: a Whisper outage (or a chunk
                # extraction failure, #556) here must not fail or defer an
                # already-cut episode. Return the failed status so the
                # pass-2 record is still persisted (#482).
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
            # Pass the feed PK so the cue analyzer can select the per-feed
            # template matcher; without it the verification pass would only
            # ever see the spectral fallback even when the feed has templates.
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
            progress_callback=progress_callback,
            audio_analysis=processed_analysis,
        )
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
                    'audio_cue_count': verification_cue_count}

        processed_ads = verification_result.get('ads', [])

        if not processed_ads:
            return {'ads': [], 'ads_processed': [], 'segments': verification_segments,
                    'status': 'clean', 'audio_cue_count': verification_cue_count}

        # Tag all ads as verification stage
        for ad in processed_ads:
            ad['detection_stage'] = 'verification'

        # Step 4: Map processed timestamps back to original-audio timestamps
        original_ads = []
        if pass1_cuts:
            timestamp_map = _build_timestamp_map(pass1_cuts)
            beep = get_replacement_duration()
            for ad in processed_ads:
                mapped = ad.copy()
                mapped['start'] = _map_to_original(ad['start'], timestamp_map, beep)
                mapped['end'] = _map_to_original(ad['end'], timestamp_map, beep)
                original_ads.append(mapped)
            logger.info(f"[{slug}:{episode_id}] Verification: mapped {len(original_ads)} ads "
                       f"to original timestamps using {len(pass1_cuts)} pass 1 cuts")
        else:
            # No pass 1 cuts means no timestamp shift -- processed = original
            original_ads = [ad.copy() for ad in processed_ads]
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
        }

    def _transcribe_verification(self, audio_path: str,
                                 podcast_name: str = None,
                                 slug: str = None) -> list[dict]:
        """Re-transcribe for verification using the shared Transcriber.

        Delegates to self.transcriber.transcribe_chunked() so episodes longer
        than the backend's single-request limit (e.g. OpenAI whisper's 25MB)
        are split into chunks instead of failing with 413. Short-circuits to
        single-shot transcribe() internally when the audio fits in one chunk.

        Lets exceptions propagate to caller so status correctly reflects
        'transcription_failed' vs 'no_segments'.
        """
        language_override = get_feed_language_override(self.db, slug)
        return self.transcriber.transcribe_chunked(
            audio_path, podcast_name, language_override=language_override,
        )


def _build_timestamp_map(pass1_cuts: list[dict]) -> list[tuple[float, float, float | None]]:
    """Build a sorted list of (cut_start, cut_duration, cut_replacement) from
    pass 1 removed ads.

    Each entry represents a gap in the original timeline that was removed.
    cut_replacement is the span's own 'replacement_duration' when present,
    else None; callers fall back to their `replacement_duration` argument
    for legacy rows without a per-span value. Used by _map_to_original to
    reverse the timestamp shift.
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


def _cut_replacement(cut: tuple, default: float | None = None) -> float | None:
    """A cut tuple's own replacement duration (third element), or `default`
    for a 2-tuple or an explicit None third element.

    Shared by _map_to_original and _map_correction_to_processed so both
    honor the same 2-tuple/3-tuple compatibility rule (see
    _build_timestamp_map) from one place.
    """
    return cut[2] if len(cut) > 2 and cut[2] is not None else default


def _map_to_original(processed_time: float,
                     cuts: list[tuple[float, float]],
                     replacement_duration: float = 0.0) -> float:
    """Map a processed-audio timestamp back to original-audio timestamp.

    Walks through the sorted cuts, accumulating removed time. Each cut is
    replaced by its own replacement duration (or `replacement_duration` as
    fallback) of audio in the processed file, so a cut shifts later content
    by (duration - replacement). A timestamp inside a replacement maps just
    inside the removed span it stands in for, keeping the mapping monotonic
    (the inverse of utils.time.adjust_timestamp).
    """
    offset = 0.0
    for cut in cuts:
        cut_start, cut_duration = cut[0], cut[1]
        cut_replacement = _cut_replacement(cut, replacement_duration)
        # In processed timeline, this cut's replacement starts at
        # cut_start - offset and lasts cut_replacement.
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
    """Map a user-correction range from original time to processed-audio time.

    Pass 1 already removed `cuts` (in original time). For a user-flagged
    region `[orig_start, orig_end)`, return the visible portion in
    processed-audio coordinates. Returns None when the entire range was
    removed by a cut (no representation in processed audio).

    Cuts are pre-sorted by start ascending (2-tuples or 3-tuples, see
    _cut_replacement). If a cut contains `orig_start`, the visible portion
    begins at the cut's end. If a cut contains `orig_end`, the visible
    portion ends at the cut's start. Then both endpoints are shifted left by
    the total cut duration that ended at or before them.
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

    # After the snap loop the endpoints are never strictly inside a cut, so
    # the forward shift is exactly adjust_timestamp's: one owner for the
    # (duration - replacement) render model. Each cut's own replacement
    # carries through so a beeped span in the mix shifts nothing.
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
