"""
Unified audio analysis facade.

Orchestrates volume and transition analysis to provide
audio signals for ad detection.
"""

import logging
import time
import os
from typing import Dict, List, Optional, Any, Tuple, Callable
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError

from .base import AudioSegmentSignal, AudioAnalysisResult, SignalType
from .volume_analyzer import VolumeAnalyzer
from .transition_detector import TransitionDetector

# Import from utils for consistent audio duration implementation
from utils.audio import get_audio_duration

logger = logging.getLogger('podcast.audio_analysis')


# Default timeout multipliers (seconds per minute of audio)
DEFAULT_VOLUME_TIMEOUT_MULTIPLIER = 2.0    # ~2s per min of audio

# Minimum timeouts regardless of duration
MIN_VOLUME_TIMEOUT = 180    # 3 minutes


def calculate_component_timeouts(duration_seconds: float) -> Dict[str, int]:
    """
    Calculate per-component timeouts based on episode duration.

    Returns timeouts in seconds for each analysis component.
    Longer episodes get proportionally longer timeouts.
    """
    duration_minutes = duration_seconds / 60.0

    return {
        'volume': max(MIN_VOLUME_TIMEOUT, int(duration_minutes * DEFAULT_VOLUME_TIMEOUT_MULTIPLIER)),
    }


class AudioAnalyzer:
    """
    Unified audio analysis for podcast ad detection.

    Combines volume and transition analysis to provide
    signals for ad detection enforcement.
    """

    def __init__(
        self,
        db=None,
        volume_threshold_db: float = 3.0,
    ):
        """
        Initialize the audio analyzer.

        Args:
            db: Database instance for loading settings
            volume_threshold_db: dB change to flag as volume anomaly
        """
        self.db = db

        # Load settings from database if available
        settings = self._load_settings()

        # Initialize volume analyzer with settings
        self.volume_analyzer = VolumeAnalyzer(
            anomaly_threshold_db=settings.get('volume_threshold_db', volume_threshold_db)
        )

        # Initialize transition detector
        from config import TRANSITION_THRESHOLD_DB, MIN_TRANSITION_AD_DURATION, MAX_TRANSITION_AD_DURATION
        self.transition_detector = TransitionDetector(
            transition_threshold_db=settings.get('transition_threshold_db', TRANSITION_THRESHOLD_DB),
            min_ad_duration=MIN_TRANSITION_AD_DURATION,
            max_ad_duration=MAX_TRANSITION_AD_DURATION,
        )

    def _load_settings(self) -> Dict[str, Any]:
        """Load settings from database."""
        settings = {}
        if self.db:
            try:
                # Numeric settings
                vol_threshold = self.db.get_setting('volume_threshold_db')
                if vol_threshold:
                    settings['volume_threshold_db'] = float(vol_threshold)

                transition_threshold = self.db.get_setting('transition_threshold_db')
                if transition_threshold:
                    settings['transition_threshold_db'] = float(transition_threshold)

            except Exception as e:
                logger.warning(f"Failed to load audio analysis settings: {e}")

        return settings

    def is_enabled(self) -> bool:
        """Audio analysis is always enabled (volume-only is lightweight, uses ffmpeg)."""
        return True

    def get_availability(self) -> Dict[str, bool]:
        """Get availability status of each analyzer."""
        return {
            'volume': True,
        }

    def _run_component_with_timeout(
        self,
        name: str,
        func: Callable,
        timeout: int
    ) -> Tuple[Any, Optional[str]]:
        """
        Run an analysis component with timeout protection.

        Uses ThreadPoolExecutor for cross-platform timeout support.
        Returns (result, error) tuple - result is None if timeout/error occurred.
        """
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(func)
            try:
                result = future.result(timeout=timeout)
                return result, None
            except FuturesTimeoutError:
                error_msg = f"{name} analysis exceeded {timeout}s timeout"
                logger.warning(error_msg)
                return None, error_msg
            except Exception as e:
                error_msg = f"{name} analysis failed: {type(e).__name__}: {e}"
                logger.warning(error_msg)
                return None, error_msg

    def analyze(
        self,
        audio_path: str,
        transcript_segments: Optional[List[Dict]] = None,
        run_parallel: bool = False,
        status_callback: Optional[callable] = None
    ) -> AudioAnalysisResult:
        """
        Run audio analysis (volume + transition detection).

        Args:
            audio_path: Path to the audio file
            transcript_segments: Optional transcript (unused, kept for API compat)
            run_parallel: Unused (kept for API compatibility)
            status_callback: Optional callback(stage, progress) for status updates

        Returns:
            AudioAnalysisResult with all detected signals
        """
        start_time = time.time()

        result = AudioAnalysisResult()

        if not os.path.exists(audio_path):
            result.errors.append(f"Audio file not found: {audio_path}")
            return result

        logger.info(f"Starting audio analysis: {audio_path}")

        # Get audio duration for timeout calculation
        duration = get_audio_duration(audio_path)
        if duration:
            timeouts = calculate_component_timeouts(duration)
            logger.info(f"Audio duration: {duration/60:.1f} min, "
                       f"timeout: volume={timeouts['volume']}s")
        else:
            timeouts = {'volume': MIN_VOLUME_TIMEOUT * 2}
            logger.warning("Could not determine audio duration, using default timeouts")

        signals = []
        errors = []
        baseline = None
        frames = []

        # Volume analysis
        if status_callback:
            status_callback("analyzing: volume", 30)

        vol_result, error = self._run_component_with_timeout(
            'volume',
            lambda: self.volume_analyzer.analyze(audio_path),
            timeouts['volume']
        )

        if error:
            errors.append(error)
            logger.warning(f"Volume analysis skipped: {error}")
        elif vol_result:
            vol_signals, vol_baseline, vol_frames = vol_result
            signals.extend(vol_signals)
            baseline = vol_baseline
            frames = vol_frames
            logger.info(f"Volume analysis complete: {len(vol_signals)} signals, {len(vol_frames)} frames")

        # Transition detection (runs on volume frames, no extra I/O)
        if frames and self.transition_detector:
            if status_callback:
                status_callback("analyzing: transitions", 35)
            try:
                transition_signals = self.transition_detector.detect_and_pair(frames)
                signals.extend(transition_signals)
                if transition_signals:
                    logger.info(f"Transition detection: {len(transition_signals)} DAI transition pairs")
            except Exception as e:
                error_msg = f"Transition detection failed: {e}"
                logger.warning(error_msg)
                errors.append(error_msg)

        result.signals = signals
        result.errors = errors
        result.loudness_baseline = baseline
        result.loudness_frames = frames
        result.analysis_time_seconds = time.time() - start_time

        # Log summary
        if errors:
            logger.warning(f"Audio analysis completed with {len(errors)} errors: {errors}")
        else:
            logger.info(f"Audio analysis complete: {len(signals)} total signals")

        return result
