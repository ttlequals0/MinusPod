"""
Volume/loudness analysis for detecting dynamically inserted ads.

Key insight: Dynamically inserted ads are often mastered louder and with
more compression than host content, causing noticeable volume changes.
"""

import subprocess
import json
import logging
import re
from typing import List, Tuple, Optional
import os

from .base import AudioSegmentSignal, LoudnessFrame, SignalType
from utils.audio import get_audio_duration

logger = logging.getLogger('podcast.audio_analysis.volume')


class VolumeAnalyzer:
    """
    Analyzes volume/loudness patterns to detect ad transitions.

    Uses ffmpeg's ebur128 filter in single-pass mode to measure loudness
    and detect regions where volume differs significantly from baseline.
    """

    def __init__(
        self,
        frame_duration: float = 5.0,
        anomaly_threshold_db: float = 3.0,
        min_anomaly_duration: float = 15.0
    ):
        """
        Initialize the volume analyzer.

        Args:
            frame_duration: Analysis window size in seconds for grouping
            anomaly_threshold_db: dB deviation from baseline to flag as anomaly
            min_anomaly_duration: Minimum duration to report as anomaly
        """
        self.frame_duration = frame_duration
        self.anomaly_threshold_db = anomaly_threshold_db
        self.min_anomaly_duration = min_anomaly_duration

    def analyze(self, audio_path: str) -> Tuple[List[AudioSegmentSignal], Optional[float]]:
        """
        Analyze audio for volume anomalies using single-pass ebur128.

        Args:
            audio_path: Path to the audio file

        Returns:
            Tuple of (list of volume anomaly signals, baseline loudness in LUFS)
        """
        if not os.path.exists(audio_path):
            logger.error(f"Audio file not found: {audio_path}")
            return [], None

        # Get audio duration
        duration = self._get_duration(audio_path)
        if duration is None or duration < self.frame_duration:
            logger.warning(f"Audio too short for volume analysis: {duration}s")
            return [], None

        logger.info(f"Analyzing volume for {duration:.1f}s audio ({duration/60:.1f} min)")

        # Single-pass loudness measurement
        frames = self._measure_loudness_single_pass(audio_path, duration)
        if not frames:
            logger.warning("No loudness frames extracted")
            return [], None

        # Calculate baseline
        loudness_values = [f.loudness_lufs for f in frames if f.loudness_lufs > -70]
        if not loudness_values:
            logger.warning("No valid loudness measurements")
            return [], None

        # Use median as baseline (robust to outliers)
        loudness_values.sort()
        mid = len(loudness_values) // 2
        baseline = loudness_values[mid]

        logger.info(f"Loudness baseline: {baseline:.1f} LUFS, {len(frames)} frames analyzed")

        # Find anomalies
        anomalies = self._find_anomalies(frames, baseline)

        logger.info(f"Found {len(anomalies)} volume anomalies")
        return anomalies, baseline

    def _get_duration(self, audio_path: str) -> Optional[float]:
        """Get audio duration using ffprobe.

        Delegates to utils.audio.get_audio_duration for consistency.
        """
        return get_audio_duration(audio_path)

    def _measure_loudness_single_pass(
        self,
        audio_path: str,
        total_duration: float
    ) -> List[LoudnessFrame]:
        """
        Measure loudness using single-pass ebur128 filter.

        This runs ffmpeg once on the entire file, parsing the per-frame
        loudness measurements from stderr. Much faster than frame-by-frame.
        """
        try:
            # Run ebur128 with verbose framelog to get per-frame measurements
            # Output format: [Parsed_ebur128_0 @ ...] t: 0.3     M: -23.5 S: -22.1 ...
            # Note: -v verbose is needed for filter output to appear in stderr
            cmd = [
                'ffmpeg', '-v', 'verbose',
                '-i', audio_path,
                '-af', 'ebur128=framelog=verbose:peak=sample',
                '-f', 'null', '-'
            ]

            # Calculate timeout based on duration - ~1 minute per hour of audio
            timeout = max(300, int(total_duration / 60) * 60 + 120)
            logger.debug(f"Running ebur128 analysis with {timeout}s timeout")

            # Don't use text=True - FFMPEG can output non-UTF-8 characters
            # which would cause UnicodeDecodeError
            result = subprocess.run(
                cmd, capture_output=True, timeout=timeout
            )

            # Safely decode stderr, replacing any non-UTF-8 characters
            try:
                stderr_text = result.stderr.decode('utf-8', errors='replace')
            except Exception:
                stderr_text = str(result.stderr)[:10000]

            # Parse ebur128 output from stderr
            # Lines look like: [Parsed_ebur128_0 @ 0x...] t: 5.0    M: -23.5 S: -22.1 ...
            raw_measurements = self._parse_ebur128_output(stderr_text)

            if not raw_measurements:
                logger.warning("No ebur128 measurements found in output")
                # Log ffmpeg return code and lines containing ebur128 data patterns
                stderr_lines = stderr_text.split('\n')
                # Find lines that look like ebur128 output (contain "t:" and "M:")
                ebur_lines = [l for l in stderr_lines if 't:' in l and 'M:' in l]
                if ebur_lines:
                    sample = '\n'.join(ebur_lines[:5])
                    logger.warning(
                        f"ffmpeg ebur128 - returncode={result.returncode}, "
                        f"found {len(ebur_lines)} data lines but regex didn't match:\n{sample}"
                    )
                else:
                    # No ebur128 lines found at all - show more stderr
                    sample = '\n'.join(stderr_lines[:20])
                    logger.warning(
                        f"ffmpeg ebur128 - returncode={result.returncode}, "
                        f"no ebur128 data lines found in {len(stderr_lines)} lines:\n{sample}"
                    )
                return []

            logger.debug(f"Parsed {len(raw_measurements)} raw measurements")

            # Group measurements into frames
            frames = self._group_into_frames(raw_measurements, total_duration)

            return frames

        except subprocess.TimeoutExpired:
            logger.error(f"ebur128 analysis timeout after {timeout}s")
            return []
        except Exception as e:
            logger.error(f"Single-pass loudness measurement failed: {e}")
            return []

    def _parse_ebur128_output(self, stderr: str) -> List[Tuple[float, float, float]]:
        """
        Parse ebur128 verbose output to extract measurements.

        Returns list of (timestamp, momentary_lufs, sample_peak) tuples.
        """
        measurements = []

        # Pattern for ebur128 output lines
        # Example: [Parsed_ebur128_0 @ 0x...] t: 0.1  TARGET:-23 LUFS    M: -23.5 S: -22.1 ...
        # Note: TARGET field appears between t: and M: in verbose output
        pattern = re.compile(
            r'\[Parsed_ebur128_0.*?\]\s+t:\s*([\d.]+)\s+'
            r'.*?'                # Allow TARGET and other fields between t: and M:
            r'M:\s*([-\d.]+)\s+'  # Momentary loudness
            r'S:\s*([-\d.]+)',    # Short-term loudness
            re.IGNORECASE
        )

        # Also try to capture peak if available
        peak_pattern = re.compile(
            r'\[Parsed_ebur128_0.*?\]\s+.*?SPK:\s*([-\d.]+)',
            re.IGNORECASE
        )

        for line in stderr.split('\n'):
            match = pattern.search(line)
            if match:
                try:
                    timestamp = float(match.group(1))
                    momentary = float(match.group(2))
                    # Use momentary loudness (M) as it's most responsive to changes
                    peak = -1.0  # Default peak

                    # Try to get peak from same line
                    peak_match = peak_pattern.search(line)
                    if peak_match:
                        peak = float(peak_match.group(1))

                    measurements.append((timestamp, momentary, peak))
                except (ValueError, IndexError):
                    continue

        return measurements

    def _group_into_frames(
        self,
        measurements: List[Tuple[float, float, float]],
        total_duration: float
    ) -> List[LoudnessFrame]:
        """
        Group raw measurements into frames of frame_duration seconds.

        Takes average loudness within each frame window.
        """
        if not measurements:
            return []

        frames = []
        frame_start = 0.0

        while frame_start < total_duration:
            frame_end = min(frame_start + self.frame_duration, total_duration)

            # Get measurements within this frame
            frame_measurements = [
                (m[1], m[2]) for m in measurements
                if frame_start <= m[0] < frame_end
            ]

            if frame_measurements:
                # Average loudness for the frame
                avg_loudness = sum(m[0] for m in frame_measurements) / len(frame_measurements)
                max_peak = max(m[1] for m in frame_measurements)

                frames.append(LoudnessFrame(
                    start=frame_start,
                    end=frame_end,
                    loudness_lufs=avg_loudness,
                    peak_dbfs=max_peak
                ))

            frame_start = frame_end

        return frames

    def _find_anomalies(
        self,
        frames: List[LoudnessFrame],
        baseline: float
    ) -> List[AudioSegmentSignal]:
        """Find regions where volume deviates significantly from baseline."""
        anomalies = []
        in_anomaly = False
        anomaly_start = 0.0
        anomaly_type = ""
        deviations = []

        for frame in frames:
            deviation = frame.loudness_lufs - baseline

            if abs(deviation) > self.anomaly_threshold_db:
                if not in_anomaly:
                    # Start new anomaly
                    in_anomaly = True
                    anomaly_start = frame.start
                    anomaly_type = "increase" if deviation > 0 else "decrease"
                    deviations = []
                deviations.append(abs(deviation))
            else:
                if in_anomaly:
                    # End current anomaly
                    anomaly_end = frame.start
                    duration = anomaly_end - anomaly_start

                    if duration >= self.min_anomaly_duration:
                        avg_deviation = sum(deviations) / len(deviations)
                        # Confidence based on deviation magnitude
                        confidence = min(0.5 + (avg_deviation / 10), 0.95)

                        signal_type = (
                            SignalType.VOLUME_INCREASE.value
                            if anomaly_type == "increase"
                            else SignalType.VOLUME_DECREASE.value
                        )

                        anomalies.append(AudioSegmentSignal(
                            start=anomaly_start,
                            end=anomaly_end,
                            signal_type=signal_type,
                            confidence=confidence,
                            details={
                                'deviation_db': round(avg_deviation, 1),
                                'baseline_lufs': round(baseline, 1),
                                'direction': anomaly_type
                            }
                        ))

                    in_anomaly = False

        # Handle anomaly at end of audio
        if in_anomaly and frames:
            anomaly_end = frames[-1].end
            duration = anomaly_end - anomaly_start

            if duration >= self.min_anomaly_duration:
                avg_deviation = sum(deviations) / len(deviations)
                confidence = min(0.5 + (avg_deviation / 10), 0.95)

                signal_type = (
                    SignalType.VOLUME_INCREASE.value
                    if anomaly_type == "increase"
                    else SignalType.VOLUME_DECREASE.value
                )

                anomalies.append(AudioSegmentSignal(
                    start=anomaly_start,
                    end=anomaly_end,
                    signal_type=signal_type,
                    confidence=confidence,
                    details={
                        'deviation_db': round(avg_deviation, 1),
                        'baseline_lufs': round(baseline, 1),
                        'direction': anomaly_type
                    }
                ))

        return anomalies
