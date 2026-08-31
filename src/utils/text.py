"""Text utility functions.

Provides shared transcript text extraction functions.
"""

import re

from utils.time import parse_timestamp

# Edge-proximity tolerance for cut/trim boundaries. Used by the
# pattern-rewrite anchor gate (a large trimmed boundary must land within
# this distance of a transcript segment edge to be trusted) and as the base
# for the ad reviewer's prose-mismatch warning margin. Boundaries are never
# moved to segment edges; this only measures proximity.
BOUNDARY_SNAP_TOLERANCE_S = 3.0


def truncate(text: str, limit: int) -> str:
    """Cut text to limit characters, ellipsis included in the count."""
    if not text or len(text) <= limit:
        return text
    # No room for the ellipsis: text[:limit - 3] would slice from the end.
    if limit <= 3:
        return text[:max(limit, 0)]
    return text[:limit - 3].rstrip() + '...'


def parse_transcript_segments(transcript_text: str) -> list[dict]:
    """Parse VTT-formatted transcript text into segment dicts.

    Parses lines in the format:
    [HH:MM:SS.mmm --> HH:MM:SS.mmm] Text content here

    Args:
        transcript_text: Raw transcript string with timestamped lines

    Returns:
        List of dicts with 'start', 'end', 'text' keys
    """
    segments: list[dict] = []
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
    segments: list[dict],
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
    segments: list[dict],
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
    return ' '.join(
        span['text']
        for span in extract_timed_spans_in_range(
            transcript, start, end, include_partial)
    )


def extract_timed_spans_in_range(
    transcript: str,
    start: float,
    end: float,
    include_partial: bool = True,
) -> list[dict]:
    """The timed spans extract_text_in_range joins, with their char offsets.

    Each dict is {'start', 'end', 'text', 'offset'}, where offset is the index
    of that span's text inside the joined string. Callers that need to map a
    character position in the extracted text back to a timestamp use this;
    extract_text_in_range delegates here so the two cannot drift apart.
    """
    if not transcript:
        return []

    # Pattern matches: [timestamp --> timestamp] text
    pattern = r'\[(\d{1,2}:\d{2}:\d{2}(?:\.\d{1,3})?)\s*-->\s*(\d{1,2}:\d{2}:\d{2}(?:\.\d{1,3})?)\]\s*([^\[]+)'

    spans: list[dict] = []
    offset = 0
    for match in re.finditer(pattern, transcript):
        seg_start = parse_timestamp(match.group(1))
        seg_end = parse_timestamp(match.group(2))
        text = match.group(3).strip()

        if not text:
            continue

        if include_partial:
            in_range = seg_end >= start and seg_start <= end
        else:
            in_range = seg_start >= start and seg_end <= end
        if not in_range:
            continue

        spans.append({'start': seg_start, 'end': seg_end,
                      'text': text, 'offset': offset})
        # +1 for the single space ' '.join inserts between spans.
        offset += len(text) + 1

    return spans


def extract_text_from_segments(
    segments: list[dict],
    start: float,
    end: float,
    max_words: int | None = None
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
    spans = timed_spans_from_segments(segments, start, end)
    if not max_words:
        return ' '.join(span['text'] for span in spans)
    words: list[str] = []
    for span in spans:
        words.extend(span['text'].split())
        if len(words) >= max_words:
            break
    return ' '.join(words[:max_words])


def timed_spans_from_segments(
    segments: list[dict],
    start: float,
    end: float,
) -> list[dict]:
    """The spans extract_text_from_segments joins, with their char offsets.

    Segments-shaped sibling of extract_timed_spans_in_range, so callers holding
    segment dicts can map a character position back to a timestamp.
    extract_text_from_segments joins this, so the two cannot drift apart.
    """
    spans: list[dict] = []
    offset = 0
    for seg in segments:
        seg_start = seg.get('start', 0)
        seg_end = seg.get('end', 0)
        if seg_end < start or seg_start > end:
            continue
        text = (seg.get('text') or '').strip()
        if not text:
            continue
        spans.append({'start': seg_start, 'end': seg_end,
                      'text': text, 'offset': offset})
        offset += len(text) + 1
    return spans
