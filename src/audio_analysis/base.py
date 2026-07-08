"""
Base data structures for audio analysis.
"""

from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
from enum import Enum


class SignalType(Enum):
    """Types of audio signals that can be detected."""
    VOLUME_INCREASE = "volume_increase"
    VOLUME_DECREASE = "volume_decrease"
    DAI_TRANSITION_PAIR = "dai_transition_pair"
    AUDIO_CUE = "audio_cue"  # Short non-spoken ding/stinger before an ad break (issue #350)


@dataclass
class AudioSegmentSignal:
    """
    Represents an audio signal detected in a time range.

    Attributes:
        start: Start time in seconds
        end: End time in seconds
        signal_type: Type of signal (volume_change, dai_transition_pair, etc.)
        confidence: Confidence score from 0.0 to 1.0
        details: Additional analyzer-specific data
    """
    start: float
    end: float
    signal_type: str
    confidence: float
    details: Dict[str, Any] = field(default_factory=dict)

    @property
    def duration(self) -> float:
        """Duration of this signal in seconds."""
        return self.end - self.start

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            'start': self.start,
            'end': self.end,
            'signal_type': self.signal_type,
            'confidence': self.confidence,
            'duration': self.duration,
            'details': self.details
        }


@dataclass
class LoudnessFrame:
    """
    Loudness measurement for a single analysis frame.

    Attributes:
        start: Frame start time in seconds
        end: Frame end time in seconds
        loudness_lufs: Integrated loudness in LUFS
        peak_dbfs: True peak in dBFS
    """
    start: float
    end: float
    loudness_lufs: float
    peak_dbfs: float = 0.0


@dataclass
class AudioAnalysisResult:
    """
    Combined results from all audio analyzers.

    Attributes:
        signals: List of all detected audio signals
        loudness_baseline: Median loudness of the episode in LUFS
        loudness_frames: Raw loudness frames from volume analysis
        analysis_time_seconds: How long the analysis took
        errors: List of any errors that occurred during analysis
    """
    signals: List[AudioSegmentSignal] = field(default_factory=list)
    loudness_baseline: Optional[float] = None
    loudness_frames: List[LoudnessFrame] = field(default_factory=list)
    analysis_time_seconds: float = 0.0
    errors: List[str] = field(default_factory=list)
    # Near-miss telemetry (#350). Advisory only -- never signals.
    cue_near_misses: List[Dict[str, Any]] = field(default_factory=list)
    # Silence spans from silencedetect (Phase B). Advisory only -- never signals.
    silence_spans: List[Dict[str, Any]] = field(default_factory=list)
    # Resolved silence-snap tunables from the analyzer pass; set when silence
    # detection ran so processing.py can reuse them without a second DB read.
    silence_tunables: Optional[Dict[str, Any]] = None
    # Cross-fetch differential result (Layer 3). Set by processing after the
    # analyzer pass, never by the analyzer itself.
    dai_differential: Optional[Dict[str, Any]] = None
    # Splice-evidence payload (spec 2.1): {'version', 'events', 'calibration'}.
    # None when the detector did not run. Evidence only -- never cuts alone.
    splice_evidence: Optional[Dict[str, Any]] = None

    def get_signals_by_type(self, signal_type: str) -> List[AudioSegmentSignal]:
        """Get all signals of a specific type."""
        return [s for s in self.signals if s.signal_type == signal_type]

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        out = {
            'signals': [s.to_dict() for s in self.signals],
            'loudness_baseline': self.loudness_baseline,
            'analysis_time_seconds': self.analysis_time_seconds,
            'errors': self.errors
        }
        # Only emit near-misses when present so the common empty case does not
        # bloat the stored analysis JSON. No from_dict exists to keep in sync.
        if self.cue_near_misses:
            out['cue_near_misses'] = self.cue_near_misses
        # Only emit silence_spans when present (same rationale as cue_near_misses).
        if self.silence_spans:
            out['silence_spans'] = self.silence_spans
        # Only emit dai_differential when set (Layer 3; stored separately at
        # episode level, but validators consume it through this dict).
        if self.dai_differential:
            out['dai_differential'] = self.dai_differential
        if self.splice_evidence is not None:
            out['splice_evidence'] = self.splice_evidence
        return out
