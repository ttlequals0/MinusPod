"""
Verification pass for ad detection.

After the first pass detects and removes ads, this module re-transcribes
the processed audio and runs detection again with a "what doesn't belong"
prompt to catch missed ads. If found, it re-cuts the pass 1 output.
"""

import logging
from typing import Dict, List, Optional

logger = logging.getLogger('podcast.verification')


class VerificationPass:
    """
    Runs the full detection pipeline on processed audio to find missed ads.

    The verification pass:
    1. Re-transcribes the pass 1 output on CPU
    2. Runs audio analysis (volume + transitions)
    3. Runs Claude detection with verification prompt
    4. Runs audio enforcement on verification results
    5. Returns ads in processed-audio timestamps for re-cutting
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
               episode_description: str = None,
               podcast_description: str = None,
               skip_patterns: bool = False,
               progress_callback=None) -> Dict:
        """
        Run full pipeline on processed audio to find missed ads.

        Returns dict with:
            'ads': list of ad dicts in processed-audio timestamps
            'segments': transcript segments from verification
            'status': 'clean', 'found_ads', or 'no_segments'
        """
        # Step 1: Re-transcribe processed audio on CPU
        logger.info(f"[{slug}:{episode_id}] Verification: Re-transcribing processed audio on CPU")
        verification_segments = self._transcribe_on_cpu(processed_audio_path)

        if not verification_segments:
            logger.warning(f"[{slug}:{episode_id}] Verification: No segments from re-transcription")
            return {'ads': [], 'segments': [], 'status': 'no_segments'}

        logger.info(f"[{slug}:{episode_id}] Verification: {len(verification_segments)} segments "
                    f"from re-transcription")

        # Step 2: Audio analysis on processed audio
        processed_analysis = None
        try:
            processed_analysis = self.audio_analyzer.analyze(processed_audio_path)
            if processed_analysis and processed_analysis.signals:
                logger.info(f"[{slug}:{episode_id}] Verification: "
                           f"{len(processed_analysis.signals)} audio signals")
        except Exception as e:
            logger.warning(f"[{slug}:{episode_id}] Verification audio analysis failed: {e}")

        # Step 3: Claude detection with verification prompt
        verification_result = self.ad_detector.run_verification_detection(
            verification_segments, podcast_name, episode_title,
            slug, episode_id, episode_description,
            podcast_description=podcast_description,
            progress_callback=progress_callback,
        )
        processed_ads = verification_result.get('ads', [])

        # Step 4: Audio enforcement on verification results
        if processed_analysis and processed_analysis.signals and processed_ads is not None:
            try:
                from audio_enforcer import AudioEnforcer
                enforcer = AudioEnforcer(sponsor_service=self.sponsor_service)
                processed_ads = enforcer.enforce(
                    processed_ads, processed_analysis, verification_segments,
                    slug=slug, episode_id=episode_id
                )
            except Exception as e:
                logger.warning(f"[{slug}:{episode_id}] Verification enforcement failed: {e}")

        if not processed_ads:
            return {'ads': [], 'segments': verification_segments, 'status': 'clean'}

        # Tag all ads as verification stage
        for ad in processed_ads:
            ad['detection_stage'] = 'verification'

        logger.info(f"[{slug}:{episode_id}] Verification found {len(processed_ads)} missed ads")
        return {
            'ads': processed_ads,
            'segments': verification_segments,
            'status': 'found_ads'
        }

    def _transcribe_on_cpu(self, audio_path: str) -> List[Dict]:
        """Transcribe using faster_whisper directly on CPU.

        GPU model is already unloaded after first pass transcription.
        We create a fresh CPU model to avoid polluting WhisperModelSingleton state.
        """
        try:
            from faster_whisper import WhisperModel
            from transcriber import WhisperModelSingleton

            model_size = WhisperModelSingleton.get_configured_model()
            logger.info(f"Verification: Loading {model_size} model on CPU for re-transcription")

            model = WhisperModel(model_size, device="cpu", compute_type="int8")
            segments_gen, info = model.transcribe(audio_path, beam_size=5)

            segments = []
            for seg in segments_gen:
                text = seg.text.strip()
                if text:
                    segments.append({
                        'start': seg.start,
                        'end': seg.end,
                        'text': text
                    })

            del model
            logger.info(f"Verification: CPU transcription complete, {len(segments)} segments")
            return segments

        except Exception as e:
            logger.error(f"Verification CPU transcription failed: {e}")
            return []
