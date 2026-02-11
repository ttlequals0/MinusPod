"""
Verification pass for ad detection.

After the first pass detects and removes ads, this module re-transcribes
the processed audio and runs detection again with a "what doesn't belong"
prompt to catch missed ads. Returns dual timestamps: original-audio
coordinates for UI/DB and processed-audio coordinates for cutting.
"""

import logging
from typing import Dict, List, Tuple

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
                 sponsor_service=None, db=None):
        self.ad_detector = ad_detector
        self.transcriber = transcriber
        self.audio_analyzer = audio_analyzer
        self.sponsor_service = sponsor_service
        self.db = db

    def verify(self, processed_audio_path: str, podcast_name: str,
               episode_title: str, slug: str, episode_id: str,
               pass1_cuts: List[Dict] = None,
               episode_description: str = None,
               podcast_description: str = None,
               skip_patterns: bool = False,
               progress_callback=None) -> Dict:
        """
        Run full pipeline on processed audio to find missed ads.

        Args:
            pass1_cuts: List of ad dicts removed in pass 1 (need start/end).
                        Used to build the timestamp map back to original audio.

        Returns dict with:
            'ads': list of ad dicts in ORIGINAL-audio timestamps (for UI/DB)
            'ads_processed': list of ad dicts in PROCESSED-audio timestamps (for cutting)
            'segments': transcript segments from verification
            'status': 'clean', 'found_ads', 'no_segments', or 'transcription_failed'
        """
        # Step 1: Re-transcribe processed audio on GPU
        if progress_callback:
            progress_callback("transcribing", 85)
        logger.info(f"[{slug}:{episode_id}] Verification: Re-transcribing processed audio on GPU")
        verification_segments = self._transcribe_on_gpu(processed_audio_path)

        if not verification_segments:
            logger.warning(f"[{slug}:{episode_id}] Verification: No segments from re-transcription")
            return {'ads': [], 'ads_processed': [], 'segments': [], 'status': 'no_segments'}

        logger.info(f"[{slug}:{episode_id}] Verification: {len(verification_segments)} segments "
                    f"from re-transcription")

        # Step 2: Audio analysis on processed audio
        if progress_callback:
            progress_callback("analyzing", 88)
        processed_analysis = None
        try:
            processed_analysis = self.audio_analyzer.analyze(processed_audio_path)
            if processed_analysis and processed_analysis.signals:
                logger.info(f"[{slug}:{episode_id}] Verification: "
                           f"{len(processed_analysis.signals)} audio signals")
        except Exception as e:
            logger.warning(f"[{slug}:{episode_id}] Verification audio analysis failed: {e}")

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
        processed_ads = verification_result.get('ads', [])

        if not processed_ads:
            return {'ads': [], 'ads_processed': [], 'segments': verification_segments,
                    'status': 'clean'}

        # Tag all ads as verification stage
        for ad in processed_ads:
            ad['detection_stage'] = 'verification'

        # Step 4: Map processed timestamps back to original-audio timestamps
        original_ads = []
        if pass1_cuts:
            timestamp_map = _build_timestamp_map(pass1_cuts)
            for ad in processed_ads:
                mapped = ad.copy()
                mapped['start'] = _map_to_original(ad['start'], timestamp_map)
                mapped['end'] = _map_to_original(ad['end'], timestamp_map)
                original_ads.append(mapped)
            logger.info(f"[{slug}:{episode_id}] Verification: mapped {len(original_ads)} ads "
                       f"to original timestamps using {len(pass1_cuts)} pass 1 cuts")
        else:
            # No pass 1 cuts means no timestamp shift -- processed = original
            original_ads = [ad.copy() for ad in processed_ads]
            logger.info(f"[{slug}:{episode_id}] Verification: no pass 1 cuts, "
                       f"timestamps are already original")

        logger.info(f"[{slug}:{episode_id}] Verification found {len(processed_ads)} missed ads")
        return {
            'ads': original_ads,
            'ads_processed': processed_ads,
            'segments': verification_segments,
            'status': 'found_ads'
        }

    def _transcribe_on_gpu(self, audio_path: str) -> List[Dict]:
        """Re-transcribe using WhisperModelSingleton (GPU).

        Lets exceptions propagate to caller so status correctly reflects
        'transcription_failed' vs 'no_segments'.
        """
        from transcriber import WhisperModelSingleton

        model_size = WhisperModelSingleton.get_configured_model()
        logger.info(f"Verification: Loading {model_size} model on GPU for re-transcription")

        # get_instance() lazy-loads GPU model if unloaded
        base_model, pipeline = WhisperModelSingleton.get_instance()

        segments_gen, info = pipeline.transcribe(
            audio_path,
            batch_size=16,
            beam_size=5,
        )

        segments = []
        for seg in segments_gen:
            text = seg.text.strip()
            if text:
                segments.append({
                    'start': seg.start,
                    'end': seg.end,
                    'text': text
                })

        logger.info(f"Verification: GPU transcription complete, {len(segments)} segments")
        return segments


def _build_timestamp_map(pass1_cuts: List[Dict]) -> List[Tuple[float, float]]:
    """Build a sorted list of (cut_start, cut_duration) from pass 1 removed ads.

    Each entry represents a gap in the original timeline that was removed.
    Used by _map_to_original to reverse the timestamp shift.
    """
    cuts = []
    for ad in pass1_cuts:
        start = ad.get('start', 0)
        end = ad.get('end', 0)
        duration = end - start
        if duration > 0:
            cuts.append((start, duration))
    cuts.sort(key=lambda x: x[0])
    return cuts


def _map_to_original(processed_time: float,
                     cuts: List[Tuple[float, float]]) -> float:
    """Map a processed-audio timestamp back to original-audio timestamp.

    Walks through the sorted cuts, accumulating removed time. For each cut
    that started before the current position in the original timeline,
    the processed time shifts forward by the cut's duration.
    """
    offset = 0.0
    for cut_start, cut_duration in cuts:
        # In original timeline, this cut starts at cut_start.
        # In processed timeline, this cut would be at cut_start - offset.
        if processed_time >= cut_start - offset:
            offset += cut_duration
        else:
            break
    return processed_time + offset
