"""Shared transcript text extraction helpers."""

import math
import re
from datetime import datetime

from utils.time import parse_timestamp

# A dropped named-zone offset is under 15h and a whole 15-minute step.
_TIMEZONE_DRIFT_MAX_SECONDS = 15 * 3600
_TIMEZONE_DRIFT_STEP_SECONDS = 15 * 60

# Edge-proximity tolerance (seconds) for cut/trim boundaries: the pattern-rewrite
# anchor gate and the base of the reviewer's prose-mismatch margin. Boundaries
# are never moved to segment edges; this only measures proximity.
BOUNDARY_SNAP_TOLERANCE_S = 3.0


def escape_like(text: str) -> str:
    """Escape LIKE metacharacters for use with ESCAPE '\\'."""
    return text.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')


def normalize_title_for_match(title: str | None) -> str:
    """Lowercase, whitespace-collapsed title for loose duplicate matching."""
    if not title:
        return ''
    return re.sub(r'\s+', ' ', title).strip().lower()


def is_timezone_drift(a: datetime, b: datetime) -> bool:
    """True when two UTC timestamps differ by the shape of a dropped named
    timezone offset: under 15h and an exact multiple of 15 minutes."""
    delta = abs((a - b).total_seconds())
    return delta < _TIMEZONE_DRIFT_MAX_SECONDS and delta % _TIMEZONE_DRIFT_STEP_SECONDS == 0


def word_boundary_re(terms) -> re.Pattern | None:
    """Case-insensitive alternation matching any of `terms` as whole words, or None if none.

    Longest first so an alternative that prefixes another cannot win.
    Lookarounds, not \\b: a term ending in a non-word character ("Yahoo!")
    has no word boundary there.
    """
    escaped = sorted({t.strip() for t in terms if t and t.strip()},
                     key=len, reverse=True)
    if not escaped:
        return None
    return re.compile(
        r'(?<!\w)(?:' + '|'.join(re.escape(t) for t in escaped) + r')(?!\w)',
        re.IGNORECASE)


def pattern_offsets(text: str, patterns: dict) -> dict[str, list[int]]:
    """Match offsets of each named pattern in `text`, by name. Names with no
    match are left out, so len() counts the names the text carries."""
    found: dict[str, list[int]] = {}
    for name, pattern in patterns.items():
        offsets = [match.start() for match in pattern.finditer(text)]
        if offsets:
            found[name] = offsets
    return found


def most_mentioned(offsets: dict[str, list[int]]) -> tuple[str | None, int]:
    """The name with the most offsets (earliest first on a tie) and its count; (None, 0) when empty."""
    if not offsets:
        return None, 0
    name = max(offsets, key=lambda n: (len(offsets[n]), -offsets[n][0]))
    return name, len(offsets[name])


def truncate(text: str, limit: int) -> str:
    """Cut text to limit characters, ellipsis included in the count."""
    if not text or len(text) <= limit:
        return text
    # No room for the ellipsis: text[:limit - 3] would slice from the end.
    if limit <= 3:
        return text[:max(limit, 0)]
    return text[:limit - 3].rstrip() + '...'


def parse_transcript_segments(transcript_text: str) -> list[dict]:
    """Parse lines like "[HH:MM:SS.mmm --> HH:MM:SS.mmm] text" into dicts with 'start', 'end', 'text'."""
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
    """Space-joined text of every segment overlapping [start_time, end_time] (seconds)."""
    texts = []
    for seg in segments:
        if seg['end'] >= start_time and seg['start'] <= end_time:
            texts.append(seg.get('text', ''))
    return ' '.join(texts)


def transcript_gaps(segments: list[dict], min_s: float) -> list[tuple[float, float]]:
    """(start, end) of every stretch of min_s or more that no segment covers, between the first and last segment."""
    gaps, covered = [], None
    for seg in segments:
        start, end = seg.get('start', 0.0), seg.get('end', 0.0)
        if covered is not None and start - covered >= min_s:
            gaps.append((covered, start))
        covered = end if covered is None else max(covered, end)
    return gaps


def get_timestamped_transcript_for_range(
    segments: list[dict],
    start_time: float,
    end_time: float,
) -> str:
    """Overlapping segments as newline-joined "[12.3-15.7] text" lines (seconds).

    Unlike get_transcript_text_for_range, each segment keeps its boundaries,
    which reviewer prompts need to emit exact trim timestamps.
    """
    lines = []
    for seg in segments:
        if seg['end'] >= start_time and seg['start'] <= end_time:
            lines.append(
                f"[{seg['start']:.1f}-{seg['end']:.1f}] {seg.get('text', '')}"
            )
    return '\n'.join(lines)


def get_timestamped_words_for_range(
    segments: list[dict],
    start_time: float,
    end_time: float,
) -> str:
    """Get timestamped words from segments that provide word timing."""
    words = []
    for segment in segments:
        for word in segment.get('words') or []:
            try:
                start = float(word['start'])
                end = float(word.get('end', start))
            except (KeyError, TypeError, ValueError):
                continue
            if not math.isfinite(start) or not math.isfinite(end) or end < start:
                continue
            if end >= start_time and start <= end_time:
                text = str(word.get('word', '')).strip()
                if text:
                    words.append(f"[{start:.2f}-{end:.2f}] {text}")
    return '\n'.join(words)


def extract_text_in_range(
    transcript: str,
    start: float,
    end: float,
    include_partial: bool = True
) -> str:
    """Space-joined text of "[HH:MM:SS.mmm --> HH:MM:SS.mmm] text" lines within [start, end] seconds.

    include_partial=False keeps only segments fully inside the range.
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
    """The timed spans extract_text_in_range joins, as {'start', 'end', 'text', 'offset'}.

    offset is the index of the span's text in the joined string, for mapping a
    character position back to a timestamp. extract_text_in_range delegates here.
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
    """Space-joined text of segment dicts within [start, end] seconds, capped at max_words if set."""
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
    """The spans extract_text_from_segments joins, with char offsets.

    Segment-dict sibling of extract_timed_spans_in_range.
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
