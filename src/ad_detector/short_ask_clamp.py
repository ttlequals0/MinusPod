"""Sentence-edge snapping for flagged short asks (kept out of utils so it can import ad_detector.boundaries)."""
from ad_detector.boundaries import timed_line_segments
from utils.short_asks import (
    EPS, SHORT_ASK_CATEGORIES, SHORT_ASK_EDGE_CAP_S, SHORT_ASK_MAX_S, SHORT_ASK_MIN_S,
    SHORT_ASK_TIGHT_BELOW_S, SHORT_ASK_TIGHT_EDGE_CAP_S, origin_of, unflag,
)

_SENTENCE_END = ('.', '?', '!')
_CLOSERS = '"\')]}\u201d\u2019'


def _ends_sentence(text):
    return (text or '').strip().rstrip(_CLOSERS).endswith(_SENTENCE_END)


def _edges(segments):
    """(starts, ends) of sentences over the shared transcript lines."""
    lines = sorted((s for s in timed_line_segments(segments or []) if 'start' in s and 'end' in s),
                   key=lambda s: s['start'])
    starts, ends = [], []
    for i, line in enumerate(lines):
        if i == 0 or _ends_sentence(lines[i - 1].get('text')):
            starts.append(float(line['start']))
        if _ends_sentence(line.get('text')) or i == len(lines) - 1:
            ends.append(float(line['end']))
    return starts, ends


def _snap(candidates, origin, current, sign):
    """Nearest candidate within the cap of the LLM edge; an edge a reviewer trimmed inward never snaps back out."""
    floor = current * sign - EPS if current * sign > origin * sign + EPS else float('-inf')
    near = [c for c in candidates
            if abs(c - origin) <= SHORT_ASK_EDGE_CAP_S + EPS and c * sign >= floor]
    return min(near, key=lambda c: (abs(c - current), abs(c - origin))) if near else None


def clamp_short_ask(ad, segments):
    """Snap a flagged marker to sentence edges within the cap of its LLM edges, else drop the flag."""
    if ad.get('short_ask') is not True:
        return ad
    origin = origin_of(ad)
    if origin is None or ad.get('category') not in SHORT_ASK_CATEGORIES:
        return unflag(ad)
    starts, ends = _edges(segments)
    start = _snap(starts, origin[0], ad['start'], 1)
    end = _snap(ends, origin[1], ad['end'], -1)
    if (start is None or end is None
            or not SHORT_ASK_MIN_S - EPS <= end - start <= SHORT_ASK_MAX_S + EPS):
        return unflag(ad)
    if end - start < SHORT_ASK_TIGHT_BELOW_S and not _edges_tight(origin, start, end):
        return unflag(ad)
    ad['start'], ad['end'] = start, end
    return ad


def _edges_tight(origin, start, end):
    """Spans under 3 s keep the flag only when both edges sit on a sentence edge within 0.5 s of the LLM's."""
    cap = SHORT_ASK_TIGHT_EDGE_CAP_S + EPS
    return abs(start - origin[0]) <= cap and abs(end - origin[1]) <= cap
