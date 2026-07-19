"""Text utility functions.

Provides shared transcript text extraction functions.
"""

import re
from typing import List, Optional

from utils.time import parse_timestamp

# Edge-proximity tolerance for cut/trim boundaries. Used by the
# pattern-rewrite anchor gate (a large trimmed boundary must land within
# this distance of a transcript segment edge to be trusted) and as the base
# for the ad reviewer's prose-mismatch warning margin. Boundaries are never
# moved to segment edges; this only measures proximity.
BOUNDARY_SNAP_TOLERANCE_S = 3.0


def parse_transcript_segments(transcript_text: str) -> List[dict]:
    """Parse VTT-formatted transcript text into segment dicts.

    Parses lines in the format:
    [HH:MM:SS.mmm --> HH:MM:SS.mmm] Text content here

    Args:
        transcript_text: Raw transcript string with timestamped lines

    Returns:
        List of dicts with 'start', 'end', 'text' keys
    """
    segments: List[dict] = []
    for line in transcript_text.split('\n'):
        if line.strip() and line.startswith('['):
            try:
                time_part, text_part = line.split('] ', 1)
                time_range = time_part.strip('[')
                start_str, end_str = time_range.split(' --> ')
                segments.append({
                    'start': parse_timestamp(start_str),
                    'end': parse_timestamp(end_str),
                    'text': text_part,
                })
            except (ValueError, TypeError):
                continue
    return segments


def get_transcript_text_for_range(
    segments: List[dict],
    start_time: float,
    end_time: float,
) -> str:
    """Get concatenated transcript text for a time range.

    Args:
        segments: List of transcript segment dicts with 'start', 'end', 'text'
        start_time: Start of range in seconds
        end_time: End of range in seconds

    Returns:
        Concatenated text from all overlapping segments
    """
    texts = []
    for seg in segments:
        if seg['end'] >= start_time and seg['start'] <= end_time:
            texts.append(seg.get('text', ''))
    return ' '.join(texts)


def get_timestamped_transcript_for_range(
    segments: List[dict],
    start_time: float,
    end_time: float,
) -> str:
    """Get per-segment timestamped transcript lines for a time range.

    Unlike get_transcript_text_for_range, which strips intra-span timestamps,
    this keeps each overlapping segment on its own line with its start/end in
    seconds so every sentence carries its boundary (reviewer prompts need
    these anchors to emit exact trim timestamps).

    Args:
        segments: List of transcript segment dicts with 'start', 'end', 'text'
        start_time: Start of range in seconds
        end_time: End of range in seconds

    Returns:
        Newline-joined lines in the form "[12.3s-15.7s] text"
    """
    lines = []
    for seg in segments:
        if seg['end'] >= start_time and seg['start'] <= end_time:
            lines.append(
                f"[{seg['start']:.1f}s-{seg['end']:.1f}s] {seg.get('text', '')}"
            )
    return '\n'.join(lines)


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
