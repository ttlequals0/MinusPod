import html
import json
import re

from config import title_matches_skip_patterns
from utils.prompt import strip_html
from utils.time import merge_cut_spans, parse_timestamp, shift_for_merged_cuts

TO_END = 'to_end'
TO_NEXT = 'to_next'
TRIM_MODES = (TO_END, TO_NEXT)
END_SNAP_SECONDS = 1.0

_LINE = re.compile(r'^\s*[(\[]?(\d{1,2}:\d{2}(?::\d{2})?)[)\]]?\s*[-:\u2013\u2014]*\s*(.+?)\s*$')

# What follows a "10:00 AM" clock time, which is not an episode offset.
CLOCK_TIME_SUFFIX = r'\s*[AaPp]\.?[Mm]\b'

# Chapter-line position only (as _LINE); clock times are left alone.
_TOKEN = re.compile(
    r'((?:^|[\n>])\s*[(\[]?)(\d{1,2}):(\d{2})(?::(\d{2}))?(?![\d:])(?!' + CLOCK_TIME_SUFFIX + ')')


def has_chapter_timestamp(description) -> bool:
    """True when the description has a token remap_description_timestamps may rewrite."""
    return bool(description) and _TOKEN.search(description) is not None


def parse_description_timestamps(description_html):
    """Return sorted (seconds, title) pairs from timestamp lines at line start."""
    anchors = {}
    for line in html.unescape(strip_html(description_html)).splitlines():
        match = _LINE.match(line)
        if not match:
            continue
        title = match.group(2).strip()
        if title:
            anchors.setdefault(parse_timestamp(match.group(1)), title)
    return sorted(anchors.items())


def _trim_span(anchors, pattern, mode, episode_duration, skip=frozenset()):
    """First anchor not in skip whose title matches pattern, as (start, end, title) or None."""
    if not pattern or not anchors or not episode_duration or episode_duration <= 0:
        return None
    patterns_json = json.dumps([pattern])
    for index, (start, title) in enumerate(anchors):
        if (start in skip or start >= episode_duration
                or not title_matches_skip_patterns(title, patterns_json)):
            continue
        end = episode_duration
        if mode == TO_NEXT and index + 1 < len(anchors):
            end = min(anchors[index + 1][0], episode_duration)
        if episode_duration - end <= END_SNAP_SECONDS:
            end = episode_duration
        return (start, end, title) if end > start else None
    return None


def description_trim_span(description, pattern, mode, episode_duration):
    """Return (start, end, title) for the first description chapter matching pattern, or None."""
    return _trim_span(parse_description_timestamps(description), pattern, mode, episode_duration)


def chapter_trim_span(chapters, pattern, mode, episode_duration, skip_starts=()):
    """description_trim_span for {startTime, title} dicts; skip_starts never match but still end the previous."""
    anchors = sorted(
        (float(ch['startTime']), ch['title']) for ch in chapters or []
        if ch.get('title') and ch.get('startTime') is not None)
    return _trim_span(anchors, pattern, mode, episode_duration,
                      skip={float(s) for s in skip_starts})


def _format_like(seconds, first, second, third):
    """Format seconds in the shape of the original token groups."""
    total = int(seconds)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if third is not None:
        return f"{hours:0{len(first)}d}:{minutes:02d}:{secs:02d}"
    total_minutes = total // 60
    return f"{total_minutes:0{len(first)}d}:{secs:02d}"


def remap_description_timestamps(description, cuts, *, replacement_duration):
    """Shift every timestamp token in a description onto the post-cut timeline."""
    if not description or not cuts:
        return description
    merged = merge_cut_spans(cuts, default_replacement=replacement_duration)

    def _sub(match):
        prefix, first, second, third = match.groups()
        if int(second) >= 60 or (third is not None and int(third) >= 60):
            return match.group(0)
        if third is None:
            original = int(first) * 60 + int(second)
        else:
            original = int(first) * 3600 + int(second) * 60 + int(third)
        shifted = shift_for_merged_cuts(original, merged)
        return prefix + _format_like(shifted, first, second, third)

    return _TOKEN.sub(_sub, description)
