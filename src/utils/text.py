"""Text utility functions.

Provides shared transcript text extraction functions.
"""

import re
from typing import List, Optional

from utils.time import parse_timestamp


def extract_text_in_range(
    transcript: str,
    start: float,
    end: float,
    include_partial: bool = True
) -> str:
    """Extract text from VTT-formatted transcript within time range.

    Parses transcript in the format:
    [HH:MM:SS.mmm --> HH:MM:SS.mmm] Text content here

    Args:
        transcript: Full transcript text with timestamps
        start: Start time in seconds
        end: End time in seconds
        include_partial: If True, include segments that partially overlap
                        the range. If False, only include fully contained.

    Returns:
        Extracted text content, joined with spaces
    """
    if not transcript:
        return ''

    # Pattern matches: [timestamp --> timestamp] text
    pattern = r'\[(\d{1,2}:\d{2}:\d{2}(?:\.\d{1,3})?)\s*-->\s*(\d{1,2}:\d{2}:\d{2}(?:\.\d{1,3})?)\]\s*([^\[]+)'

    segments: List[str] = []
    for match in re.finditer(pattern, transcript):
        seg_start = parse_timestamp(match.group(1))
        seg_end = parse_timestamp(match.group(2))
        text = match.group(3).strip()

        if not text:
            continue

        if include_partial:
            # Include if any overlap
            if seg_end >= start and seg_start <= end:
                segments.append(text)
        else:
            # Include only if fully contained
            if seg_start >= start and seg_end <= end:
                segments.append(text)

    return ' '.join(segments)


def extract_text_from_segments(
    segments: List[dict],
    start: float,
    end: float,
    max_words: Optional[int] = None
) -> str:
    """Extract text from segment dicts within time range.

    Works with segment lists (dicts with 'start', 'end', 'text' keys)
    rather than VTT strings.

    Args:
        segments: List of segment dicts with start/end/text
        start: Start time in seconds
        end: End time in seconds
        max_words: Optional maximum word count limit

    Returns:
        Extracted text content, joined with spaces
    """
    words: List[str] = []
    for seg in segments:
        seg_start = seg.get('start', 0)
        seg_end = seg.get('end', 0)

        # Include segment if it overlaps with the range
        if seg_end >= start and seg_start <= end:
            text = seg.get('text', '').strip()
            if text:
                if max_words:
                    words.extend(text.split())
                    if len(words) >= max_words:
                        break
                else:
                    words.append(text)

    if max_words:
        return ' '.join(words[:max_words])
    return ' '.join(words)
