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

    def overlaps(self, other: 'AudioSegmentSignal', tolerance: float = 0) -> bool:
        """Check if this signal overlaps with another."""
        return self.start <= other.end + tolerance and self.end >= other.start - tolerance

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

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'AudioSegmentSignal':
        """Create from dictionary."""
        return cls(
            start=data['start'],
            end=data['end'],
            signal_type=data['signal_type'],
            confidence=data['confidence'],
            details=data.get('details', {})
        )


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

    def get_signals_in_range(self, start: float, end: float) -> List[AudioSegmentSignal]:
        """Get all signals that overlap with the given time range."""
        return [
            s for s in self.signals
            if s.start < end and s.end > start
        ]

    def get_signals_by_type(self, signal_type: str) -> List[AudioSegmentSignal]:
        """Get all signals of a specific type."""
        return [s for s in self.signals if s.signal_type == signal_type]

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            'signals': [s.to_dict() for s in self.signals],
            'loudness_baseline': self.loudness_baseline,
            'analysis_time_seconds': self.analysis_time_seconds,
            'errors': self.errors
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'AudioAnalysisResult':
        """Create from dictionary."""
        signals = [AudioSegmentSignal.from_dict(s) for s in data.get('signals', [])]

        return cls(
            signals=signals,
            loudness_baseline=data.get('loudness_baseline'),
            analysis_time_seconds=data.get('analysis_time_seconds', 0.0),
            errors=data.get('errors', [])
        )
