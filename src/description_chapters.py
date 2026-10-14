import html
import json
import re

from config import title_matches_skip_patterns
from utils.prompt import strip_html
from utils.time import parse_timestamp

TO_END = 'to_end'
TO_NEXT = 'to_next'
TRIM_MODES = (TO_END, TO_NEXT)
END_SNAP_SECONDS = 1.0

_LINE = re.compile(r'^\s*[(\[]?(\d{1,2}:\d{2}(?::\d{2})?)[)\]]?\s*[-:\u2013\u2014]*\s*(.+?)\s*$')


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


def _trim_span(anchors, pattern, mode, episode_duration):
    """First anchor whose title matches pattern, as (start, end, title) or None."""
    if not pattern or not anchors or not episode_duration or episode_duration <= 0:
        return None
    patterns_json = json.dumps([pattern])
    for index, (start, title) in enumerate(anchors):
        if start >= episode_duration or not title_matches_skip_patterns(title, patterns_json):
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


def chapter_trim_span(chapters, pattern, mode, episode_duration):
    """Same as description_trim_span for a chapters list of {startTime, title} dicts."""
    anchors = sorted(
        (float(ch['startTime']), ch['title']) for ch in chapters or []
        if ch.get('title') and ch.get('startTime') is not None)
    return _trim_span(anchors, pattern, mode, episode_duration)
