"""
Unified audio analysis facade.

Orchestrates volume, music, and speaker analysis to provide
comprehensive audio signals for ad detection.
"""

import logging
import time
import os
from typing import Dict, List, Optional, Any, Tuple, Callable
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError

from .base import AudioSegmentSignal, AudioAnalysisResult, SignalType
from .volume_analyzer import VolumeAnalyzer
from .music_detector import MusicBedDetector
from .speaker_analyzer import SpeakerAnalyzer

# Import from utils for consistent audio duration implementation
from utils.audio import get_audio_duration

logger = logging.getLogger('podcast.audio_analysis')


# Default timeout multipliers (seconds per minute of audio)
DEFAULT_VOLUME_TIMEOUT_MULTIPLIER = 2.0    # ~2s per min of audio
DEFAULT_MUSIC_TIMEOUT_MULTIPLIER = 5.0     # ~5s per min of audio
DEFAULT_SPEAKER_TIMEOUT_MULTIPLIER = 8.0   # ~8s per min of audio

# Minimum timeouts regardless of duration
MIN_VOLUME_TIMEOUT = 180    # 3 minutes
MIN_MUSIC_TIMEOUT = 300     # 5 minutes
MIN_SPEAKER_TIMEOUT = 900   # 15 minutes


def calculate_component_timeouts(duration_seconds: float) -> Dict[str, int]:
    """
    Calculate per-component timeouts based on episode duration.

    Returns timeouts in seconds for each analysis component.
    Longer episodes get proportionally longer timeouts.
    """
    duration_minutes = duration_seconds / 60.0

    return {
        'volume': max(MIN_VOLUME_TIMEOUT, int(duration_minutes * DEFAULT_VOLUME_TIMEOUT_MULTIPLIER)),
        'music': max(MIN_MUSIC_TIMEOUT, int(duration_minutes * DEFAULT_MUSIC_TIMEOUT_MULTIPLIER)),
        'speaker': max(MIN_SPEAKER_TIMEOUT, int(duration_minutes * DEFAULT_SPEAKER_TIMEOUT_MULTIPLIER)),
    }


class AudioAnalyzer:
    """
    Unified audio analysis for podcast ad detection.

    Combines volume, music bed, and speaker diarization analysis
    to provide comprehensive signals that enhance Claude's detection.
    """

    def __init__(
        self,
        db=None,
        hf_token: Optional[str] = None,
        # Volume settings
        volume_threshold_db: float = 3.0,
        # Music settings
        music_threshold: float = 0.6,
        # Speaker settings
        min_monologue_duration: float = 45.0
    ):
        """
        Initialize the audio analyzer.

        Args:
            db: Database instance for loading settings
            hf_token: HuggingFace token for pyannote models
            volume_threshold_db: dB change to flag as volume anomaly
            music_threshold: Music detection confidence threshold
            min_monologue_duration: Minimum seconds for monologue detection
        """
        self.db = db
        self._hf_token = hf_token

        # Load settings from database if available
        settings = self._load_settings()

        # Initialize analyzers with settings
        self.volume_analyzer = VolumeAnalyzer(
            anomaly_threshold_db=settings.get('volume_threshold_db', volume_threshold_db)
        )
        self.music_detector = MusicBedDetector(
            music_threshold=settings.get('music_threshold', music_threshold)
        )
        self.speaker_analyzer = SpeakerAnalyzer(
            hf_token=self._hf_token or os.environ.get('HF_TOKEN'),
            min_monologue_duration=settings.get('monologue_duration_threshold', min_monologue_duration)
        )

        # Feature toggles
        self._enabled = settings.get('audio_analysis_enabled', False)
        self._volume_enabled = settings.get('volume_analysis_enabled', True)
        self._music_enabled = settings.get('music_detection_enabled', True)
        self._speaker_enabled = settings.get('speaker_analysis_enabled', True)

    def _load_settings(self) -> Dict[str, Any]:
        """Load settings from database."""
        settings = {}
        if self.db:
            try:
                settings['audio_analysis_enabled'] = (
                    self.db.get_setting('audio_analysis_enabled') == 'true'
                )
                settings['volume_analysis_enabled'] = (
                    self.db.get_setting('volume_analysis_enabled') != 'false'
                )
                settings['music_detection_enabled'] = (
                    self.db.get_setting('music_detection_enabled') != 'false'
                )
                settings['speaker_analysis_enabled'] = (
                    self.db.get_setting('speaker_analysis_enabled') != 'false'
                )

                # Numeric settings
                vol_threshold = self.db.get_setting('volume_threshold_db')
                if vol_threshold:
                    settings['volume_threshold_db'] = float(vol_threshold)

                music_threshold = self.db.get_setting('music_confidence_threshold')
                if music_threshold:
                    settings['music_threshold'] = float(music_threshold)

                mono_duration = self.db.get_setting('monologue_duration_threshold')
                if mono_duration:
                    settings['monologue_duration_threshold'] = float(mono_duration)

            except Exception as e:
                logger.warning(f"Failed to load audio analysis settings: {e}")

        return settings

    def is_enabled(self) -> bool:
        """Check if audio analysis is enabled (reads from database for live updates)."""
        if self.db:
            try:
                return self.db.get_setting('audio_analysis_enabled') == 'true'
            except Exception:
                pass
        return self._enabled

    def is_enabled_for_podcast(self, slug: str) -> bool:
        """Check if audio analysis is enabled for a specific podcast.

        Respects podcast-level override if set, otherwise falls back to global setting.
        """
        if not self.db:
            return self._enabled

        try:
            # Check for podcast-level override
            override = self.db.get_podcast_audio_analysis_override(slug)
            if override is not None:
                return override
        except Exception:
            pass

        # Fall back to global setting
        return self.is_enabled()

    def get_availability(self) -> Dict[str, bool]:
        """Get availability status of each analyzer."""
        return {
            'volume': True,  # Always available (uses ffmpeg)
            'music': self.music_detector.is_available(),
            'speaker': self.speaker_analyzer.is_available()
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
        Run comprehensive audio analysis.

        Args:
            audio_path: Path to the audio file
            transcript_segments: Optional transcript for enhanced analysis
            run_parallel: Whether to run analyzers in parallel (default False for better status updates)
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

        # Run analyses sequentially to provide granular status updates
        signals, errors, baseline = self._run_sequential_analysis(
            audio_path, transcript_segments, status_callback
        )

        result.signals = signals
        result.errors = errors
        result.loudness_baseline = baseline

        # Get speaker metrics if available
        speaker_signals = [
            s for s in signals if s.signal_type == SignalType.MONOLOGUE.value
        ]
        if speaker_signals and hasattr(self, '_last_conversation_metrics'):
            result.conversation_metrics = self._last_conversation_metrics
            result.speaker_count = self._last_conversation_metrics.num_speakers

        result.analysis_time_seconds = time.time() - start_time

        logger.info(
            f"Audio analysis complete: {len(signals)} signals, "
            f"{len(errors)} errors, {result.analysis_time_seconds:.1f}s"
        )

        return result

    def _run_parallel_analysis(
        self,
        audio_path: str,
        transcript_segments: Optional[List[Dict]]
    ) -> tuple:
        """Run volume and music analysis in parallel."""
        signals = []
        errors = []
        baseline = None

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = {}

            # Volume analysis
            if self._volume_enabled:
                futures['volume'] = executor.submit(
                    self.volume_analyzer.analyze, audio_path
                )

            # Music analysis
            if self._music_enabled and self.music_detector.is_available():
                futures['music'] = executor.submit(
                    self.music_detector.analyze, audio_path
                )

            # Collect results
            for name, future in futures.items():
                try:
                    result = future.result(timeout=600)  # 10 min timeout

                    if name == 'volume':
                        vol_signals, vol_baseline = result
                        signals.extend(vol_signals)
                        baseline = vol_baseline
                    elif name == 'music':
                        signals.extend(result)

                except Exception as e:
                    error_msg = f"{name} analysis failed: {e}"
                    logger.warning(error_msg)
                    errors.append(error_msg)

        # Speaker analysis runs separately (memory intensive)
        if self._speaker_enabled and self.speaker_analyzer.is_available():
            try:
                speaker_signals, metrics = self.speaker_analyzer.analyze(
                    audio_path, transcript_segments
                )
                signals.extend(speaker_signals)
                self._last_conversation_metrics = metrics
            except Exception as e:
                error_msg = f"speaker analysis failed: {e}"
                logger.warning(error_msg)
                errors.append(error_msg)

        return signals, errors, baseline

    def _run_sequential_analysis(
        self,
        audio_path: str,
        transcript_segments: Optional[List[Dict]],
        status_callback: Optional[callable] = None
    ) -> tuple:
        """
        Run all analyses sequentially with status updates and timeouts.

        Implements graceful degradation - if one component fails or times out,
        the analysis continues with remaining components.
        """
        signals = []
        errors = []
        baseline = None

        # Get audio duration for timeout calculation
        duration = get_audio_duration(audio_path)
        if duration:
            timeouts = calculate_component_timeouts(duration)
            logger.info(f"Audio duration: {duration/60:.1f} min, timeouts: "
                       f"volume={timeouts['volume']}s, music={timeouts['music']}s, "
                       f"speaker={timeouts['speaker']}s")
        else:
            # Fall back to generous defaults for unknown duration
            timeouts = {
                'volume': MIN_VOLUME_TIMEOUT * 2,
                'music': MIN_MUSIC_TIMEOUT * 2,
                'speaker': MIN_SPEAKER_TIMEOUT * 2
            }
            logger.warning("Could not determine audio duration, using default timeouts")

        # Volume analysis (fast, run first)
        if self._volume_enabled:
            if status_callback:
                status_callback("analyzing: volume", 30)

            result, error = self._run_component_with_timeout(
                'volume',
                lambda: self.volume_analyzer.analyze(audio_path),
                timeouts['volume']
            )

            if error:
                errors.append(error)
                logger.warning(f"Volume analysis skipped: {error}")
            elif result:
                vol_signals, vol_baseline = result
                signals.extend(vol_signals)
                baseline = vol_baseline
                logger.info(f"Volume analysis complete: {len(vol_signals)} signals")

        # Music analysis
        if self._music_enabled and self.music_detector.is_available():
            if status_callback:
                status_callback("analyzing: music", 35)

            result, error = self._run_component_with_timeout(
                'music',
                lambda: self.music_detector.analyze(audio_path),
                timeouts['music']
            )

            if error:
                errors.append(error)
                logger.warning(f"Music analysis skipped: {error}")
            elif result:
                signals.extend(result)
                logger.info(f"Music analysis complete: {len(result)} signals")

        # Speaker analysis (slowest, run last)
        if self._speaker_enabled and self.speaker_analyzer.is_available():
            if status_callback:
                status_callback("analyzing: speakers", 40)

            result, error = self._run_component_with_timeout(
                'speaker',
                lambda: self.speaker_analyzer.analyze(audio_path, transcript_segments),
                timeouts['speaker']
            )

            if error:
                errors.append(error)
                logger.warning(f"Speaker analysis skipped: {error}")
            elif result:
                speaker_signals, metrics = result
                signals.extend(speaker_signals)
                self._last_conversation_metrics = metrics
                logger.info(f"Speaker analysis complete: {len(speaker_signals)} signals")

        # Log summary
        if errors:
            logger.warning(f"Audio analysis completed with {len(errors)} errors: {errors}")
        else:
            logger.info(f"Audio analysis complete: {len(signals)} total signals")

        return signals, errors, baseline

    def format_for_claude(
        self,
        result: AudioAnalysisResult,
        window_start: float = 0,
        window_end: float = float('inf')
    ) -> str:
        """
        Format analysis results as context for Claude prompts.

        Args:
            result: The analysis result to format
            window_start: Start of the current analysis window
            window_end: End of the current analysis window

        Returns:
            Formatted string to inject into Claude prompt
        """
        if not result.signals and not result.conversation_metrics:
            return ""

        lines = []
        lines.append("=" * 50)
        lines.append("AUDIO ANALYSIS SIGNALS")
        lines.append("(Use as supplementary context for ad detection)")
        lines.append("=" * 50)

        # Conversation type
        if result.conversation_metrics:
            metrics = result.conversation_metrics
            if metrics.is_conversational:
                lines.append(f"\nEpisode Type: CONVERSATIONAL")
                lines.append(f"  Speakers: {metrics.num_speakers}")
                lines.append(f"  Turn frequency: {metrics.turn_frequency}/min")
                lines.append(f"  Speaker balance: {metrics.speaker_balance:.0%}")
            else:
                lines.append(f"\nEpisode Type: SOLO/INTERVIEW")
                lines.append(f"  Speakers: {metrics.num_speakers}")

        # Get signals for this window
        window_signals = result.get_signals_in_range(window_start, window_end)

        # Volume changes
        volume_signals = [
            s for s in window_signals
            if s.signal_type in [SignalType.VOLUME_INCREASE.value, SignalType.VOLUME_DECREASE.value]
        ]
        if volume_signals:
            lines.append(f"\nVOLUME CHANGES:")
            for s in volume_signals:
                direction = "+" if "increase" in s.signal_type else "-"
                deviation = s.details.get('deviation_db', 0)
                lines.append(
                    f"  [{self._format_time(s.start)}] {direction}{deviation:.1f}dB "
                    f"(confidence: {s.confidence:.0%})"
                )

        # Music beds
        music_signals = [
            s for s in window_signals
            if s.signal_type == SignalType.MUSIC_BED.value
        ]
        if music_signals:
            lines.append(f"\nMUSIC BEDS DETECTED:")
            for s in music_signals:
                lines.append(
                    f"  [{self._format_time(s.start)} - {self._format_time(s.end)}] "
                    f"(confidence: {s.confidence:.0%})"
                )

        # Monologues
        mono_signals = [
            s for s in window_signals
            if s.signal_type == SignalType.MONOLOGUE.value
        ]
        if mono_signals:
            lines.append(f"\nEXTENDED MONOLOGUES:")
            for s in mono_signals:
                speaker = s.details.get('speaker', 'unknown')
                is_host = s.details.get('is_host', False)
                has_ad_lang = s.details.get('has_ad_language', False)
                host_note = " [HOST]" if is_host else ""
                ad_note = " [AD LANGUAGE DETECTED]" if has_ad_lang else ""
                lines.append(
                    f"  [{self._format_time(s.start)} - {self._format_time(s.end)}] "
                    f"{s.duration:.0f}s by {speaker}{host_note}{ad_note} "
                    f"(confidence: {s.confidence:.0%})"
                )

        # Summary note
        lines.append("")
        lines.append("-" * 50)
        if window_signals:
            lines.append("NOTE: These signals suggest potential ad transitions.")
            lines.append("Correlate with transcript content for final determination.")
        else:
            lines.append("No strong audio signals in this window.")

        return '\n'.join(lines)

    def _format_time(self, seconds: float) -> str:
        """Format seconds as MM:SS."""
        minutes = int(seconds // 60)
        secs = int(seconds % 60)
        return f"{minutes}:{secs:02d}"
