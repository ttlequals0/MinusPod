"""
Audio analysis module for podcast ad detection.

Provides volume and transition analysis to enhance ad detection
with audio-level signals.
"""

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
