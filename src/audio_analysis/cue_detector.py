"""Audio-cue (ding/stinger) detection for ad-break boundaries (issue #350).

Some shows play a short non-spoken sound -- a chime or stinger -- just before
an ad break. The transcript loses it, so the ad detector flags the ad once the
spoken sponsor copy starts, a beat late. This detector band-passes the audio to
the frequency band such cues live in, measures per-frame loudness of that band
with ffmpeg's ``ebur128`` (the same tool the volume analyzer uses), and flags
brief loudness bursts that stand out from the in-band speech baseline.

Each burst becomes an ``audio_cue`` ``AudioSegmentSignal`` fed to the LLM as a
timing hint. It is supporting evidence only: the model must still find ad
content in the transcript. This is an opt-in experiment, off by default.
"""

import logging
import os
import re
import subprocess
from statistics import median
from typing import List, Tuple

from .base import AudioSegmentSignal, SignalType
from config import (
    AUDIO_CUE_FREQ_MIN_HZ,
    AUDIO_CUE_FREQ_MAX_HZ,
    AUDIO_CUE_PROMINENCE_DB,
    AUDIO_CUE_MIN_CONFIDENCE,
    AUDIO_CUE_MIN_DURATION,
    AUDIO_CUE_MAX_DURATION,
    AUDIO_CUE_ONSET_LAG_SECONDS,
)
from utils.audio import get_audio_duration
from utils.subprocess_registry import tracked_run

logger = logging.getLogger('podcast.audio_analysis.cue')

# Momentary-loudness line from ``ebur128=framelog=verbose``. Mirrors the parse
# in volume_analyzer.py; kept local so this detector stays self-contained.
_EBUR128_LINE = re.compile(
    r'\[Parsed_ebur128.*?\]\s+t:\s*([\d.]+).*?M:\s*([-\d.]+)',
    re.IGNORECASE,
)

# ebur128 reports momentary loudness on roughly this cadence; used as the
# nominal width of a single in-burst frame so a one-frame burst is not zero
# seconds long.
_FRAME_STEP_SECONDS = 0.1

# Loudness below this is the silence floor; excluded from the baseline.
_SILENCE_FLOOR_LUFS = -70.0


class AudioCueDetector:
    """Flags short in-band loudness bursts that look like a ding/stinger."""

    def __init__(
        self,
        freq_min_hz: float = AUDIO_CUE_FREQ_MIN_HZ,
        freq_max_hz: float = AUDIO_CUE_FREQ_MAX_HZ,
        prominence_db: float = AUDIO_CUE_PROMINENCE_DB,
        min_confidence: float = AUDIO_CUE_MIN_CONFIDENCE,
        min_duration: float = AUDIO_CUE_MIN_DURATION,
        max_duration: float = AUDIO_CUE_MAX_DURATION,
    ):
        self.freq_min_hz = freq_min_hz
        self.freq_max_hz = freq_max_hz
        self.prominence_db = prominence_db
        self.min_confidence = min_confidence
        self.min_duration = min_duration
        self.max_duration = max_duration

    def detect(self, audio_path: str) -> List[AudioSegmentSignal]:
        """Return audio_cue signals for ``audio_path`` (empty on any failure)."""
        if not os.path.exists(audio_path):
            logger.warning(f"Audio file not found for cue detection: {audio_path}")
            return []

        measurements = self._measure_band_loudness(audio_path)
        if not measurements:
            # ffmpeg ran but the ebur128 momentary-loudness regex matched no
            # frames -- a format mismatch, not "no cue". Log so it is not
            # silently indistinguishable from a clean zero-cue result.
            logger.info("Audio cue: 0 in-band frames measured (no momentary-loudness output)")
            return []

        values = [m[1] for m in measurements if m[1] > _SILENCE_FLOOR_LUFS]
        if not values:
            logger.info(
                f"Audio cue: {len(measurements)} frames, all at/below silence floor "
                f"({_SILENCE_FLOOR_LUFS:.0f} LUFS) -- 0 cue(s)"
            )
            return []
        baseline = median(values)

        signals = self._find_bursts(measurements, baseline)
        # Always log the run, including the zero-cue case, so a quiet result is
        # observable: it tells whether the audio simply had no burst (peak below
        # threshold) versus the detector not running at all.
        peak = max(values) - baseline
        logger.info(
            f"Audio cue: {len(measurements)} frames, baseline {baseline:.1f} LUFS, "
            f"peak +{peak:.1f} dB vs threshold +{self.prominence_db:.1f} dB, "
            f"{len(signals)} cue(s) emitted"
        )
        return signals

    def _measure_band_loudness(self, audio_path: str) -> List[Tuple[float, float]]:
        """Band-pass then ebur128; return [(timestamp, momentary_lufs), ...]."""
        duration = get_audio_duration(audio_path)
        if duration is None:
            logger.warning("Could not determine audio duration for cue detection")
            return []

        # Band-pass to the cue's frequency band, then measure per-frame loudness
        # of just that band. K-weighting in ebur128 sits on top; we only care
        # about loudness relative to the in-band baseline, so that is fine.
        af = (
            f"highpass=f={int(self.freq_min_hz)},"
            f"lowpass=f={int(self.freq_max_hz)},"
            f"ebur128=framelog=verbose:peak=sample"
        )
        cmd = ['ffmpeg', '-v', 'verbose', '-i', audio_path, '-af', af, '-f', 'null', '-']
        # Same capped, duration-proportional timeout the volume pass uses.
        timeout = min(max(300, int(duration / 60) * 60 + 120), 1200)

        try:
            # No text=True: ffmpeg can emit non-UTF-8 bytes on stderr.
            result = tracked_run(cmd, capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            logger.error(f"Audio cue ebur128 pass timed out after {timeout}s")
            return []
        except Exception as e:
            logger.error(f"Audio cue ebur128 pass failed: {e}")
            return []

        try:
            stderr_text = result.stderr.decode('utf-8', errors='replace')
        except Exception:
            stderr_text = str(result.stderr)[:10000]

        measurements: List[Tuple[float, float]] = []
        for line in stderr_text.split('\n'):
            m = _EBUR128_LINE.search(line)
            if not m:
                continue
            try:
                measurements.append((float(m.group(1)), float(m.group(2))))
            except (ValueError, IndexError):
                continue
        return measurements

    def _find_bursts(
        self, measurements: List[Tuple[float, float]], baseline: float
    ) -> List[AudioSegmentSignal]:
        """Group consecutive above-threshold frames into candidate cues."""
        signals: List[AudioSegmentSignal] = []
        in_burst = False
        burst_start = 0.0
        last_ts = 0.0
        peak_prominence = 0.0

        for ts, loudness in measurements:
            prominence = loudness - baseline
            if prominence > self.prominence_db:
                if not in_burst:
                    in_burst = True
                    burst_start = ts
                    peak_prominence = prominence
                else:
                    peak_prominence = max(peak_prominence, prominence)
                last_ts = ts
            elif in_burst:
                self._maybe_emit(signals, burst_start, last_ts, peak_prominence, baseline)
                in_burst = False

        if in_burst:
            self._maybe_emit(signals, burst_start, last_ts, peak_prominence, baseline)

        return signals

    def _maybe_emit(self, signals, start: float, last_ts: float,
                    peak_prominence: float, baseline: float) -> None:
        end = last_ts + _FRAME_STEP_SECONDS
        duration = end - start
        if duration < self.min_duration or duration > self.max_duration:
            return
        # Confidence grows with how far the peak rose above the burst threshold.
        confidence = max(0.6, min(0.98, 0.6 + (peak_prominence - self.prominence_db) / 20.0))
        if confidence < self.min_confidence:
            return
        # ebur128 momentary loudness integrates over 400ms, so the first
        # above-threshold frame lags the true acoustic onset; pull the
        # reported start back so the cue lands on the ding, not after it.
        # Applied after the duration gates so they judge the measured burst.
        start = max(0.0, start - AUDIO_CUE_ONSET_LAG_SECONDS)
        signals.append(AudioSegmentSignal(
            start=round(start, 2),
            end=round(end, 2),
            signal_type=SignalType.AUDIO_CUE.value,
            confidence=round(confidence, 2),
            details={
                'prominence_db': round(peak_prominence, 1),
                'baseline_lufs': round(baseline, 1),
                'band_hz': [int(self.freq_min_hz), int(self.freq_max_hz)],
            },
        ))
