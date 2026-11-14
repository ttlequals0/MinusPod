"""Audio analysis for podcast ad detection: volume and transition signals."""

import logging

logger = logging.getLogger('podcast.audio_analysis')

# Export main classes
from .base import AudioSegmentSignal, AudioAnalysisResult
from .audio_analyzer import AudioAnalyzer

__all__ = [
    'AudioAnalyzer',
    'AudioSegmentSignal',
    'AudioAnalysisResult',
]
