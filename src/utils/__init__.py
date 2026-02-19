"""Shared utility functions for MinusPod.

This module provides common utilities used across the codebase:
- audio: Audio file operations (duration, metadata)
- time: Timestamp parsing, formatting, and adjustment
- text: Transcript text extraction
- gpu: GPU memory management
- constants: Shared field names and classification values
"""

from utils.audio import get_audio_duration, AudioMetadata
from utils.time import (
    parse_timestamp, format_time, format_vtt_timestamp,
    adjust_timestamp, first_not_none,
)
from utils.text import extract_text_in_range, extract_text_from_segments
from utils.gpu import clear_gpu_memory
from utils.constants import (
    INVALID_SPONSOR_VALUES, STRUCTURAL_FIELDS,
    SPONSOR_PRIORITY_FIELDS, SPONSOR_PATTERN_KEYWORDS,
    INVALID_SPONSOR_CAPTURE_WORDS, NOT_AD_CLASSIFICATIONS,
)

__all__ = [
    'get_audio_duration',
    'AudioMetadata',
    'parse_timestamp',
    'format_time',
    'format_vtt_timestamp',
    'adjust_timestamp',
    'first_not_none',
    'extract_text_in_range',
    'extract_text_from_segments',
    'clear_gpu_memory',
    'INVALID_SPONSOR_VALUES',
    'STRUCTURAL_FIELDS',
    'SPONSOR_PRIORITY_FIELDS',
    'SPONSOR_PATTERN_KEYWORDS',
    'INVALID_SPONSOR_CAPTURE_WORDS',
    'NOT_AD_CLASSIFICATIONS',
]
