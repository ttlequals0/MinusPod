"""Divider planning for splitting a merged multi-sponsor ad marker (issue #563).

One marker over back-to-back ads is right for the audio and wrong for pattern
learning: confirming it mints one oversized multi-sponsor template. These
helpers locate AD_TRANSITION_PHRASES in the span's transcript and map each match
back to a timestamp. Free of Flask and DB imports so it unit tests directly.
"""
from typing import Dict, List, Optional

from config import MIN_AD_DURATION
from sponsor_service import SponsorService
from text_pattern_matcher import AD_TRANSITION_PHRASES, find_transition_offsets


def _join(spans: List[Dict]) -> str:
    """Rebuild the text extract_timed_spans_in_range's offsets index into."""
    return ' '.join(span['text'] for span in spans)


def _time_at_offset(spans: List[Dict], offset: int) -> Optional[float]:
    """Start time of the span containing `offset` in the joined text.

    The span's own start is used rather than interpolating within it: a
    transition phrase marks the beginning of a sponsor read, and the read starts
    where that transcript segment starts.
    """
    for span in spans:
        # +1 covers the space ' '.join inserts after this span's text.
        if span['offset'] <= offset < span['offset'] + len(span['text']) + 1:
            return span['start']
    return None


def _phrase_at_offset(text: str, offset: int) -> str:
    """The longest transition phrase matching at `offset`, for display."""
    lowered = text.lower()
    matches = [p for p in AD_TRANSITION_PHRASES if lowered.startswith(p, offset)]
    return max(matches, key=len) if matches else ''


def build_split_candidates(spans: List[Dict], start: float,
                           end: float) -> List[Dict]:
    """Proposed divider times inside (start, end), earliest first.

    A candidate is dropped when it would leave a piece shorter than
    MIN_AD_DURATION on either side, so the editor never opens already invalid.
    Returns [] when the span has no transcript or no transition phrase.
    """
    if not spans:
        return []
    text = _join(spans)
    out: List[Dict] = []
    for offset in find_transition_offsets(text):
        time = _time_at_offset(spans, offset)
        if time is None:
            continue
        # A phrase at the very start of the block marks the block's own opening,
        # not a boundary between two ads inside it.
        if time - start < MIN_AD_DURATION or end - time < MIN_AD_DURATION:
            continue
        if any(abs(time - c['time']) < MIN_AD_DURATION for c in out):
            continue
        out.append({'time': time, 'phrase': _phrase_at_offset(text, offset)})
    return sorted(out, key=lambda c: c['time'])


def build_split_pieces(spans: List[Dict], start: float, end: float,
                       times: List[float]) -> List[Dict]:
    """The pieces `times` would produce, each with its text and sponsor guess.

    Boundary times outside (start, end) are ignored rather than rejected: this
    builds a preview, and validation of a submitted split lives in the
    corrections endpoint.
    """
    inner = sorted(t for t in times if start < t < end)
    bounds = [start] + inner + [end]
    pieces: List[Dict] = []
    for i in range(len(bounds) - 1):
        piece_start, piece_end = bounds[i], bounds[i + 1]
        # Positive-measure overlap: a span that only touches a boundary
        # belongs to its neighbour, not both pieces.
        text = ' '.join(
            span['text'] for span in spans
            if span['end'] > piece_start and span['start'] < piece_end
        )
        pieces.append({
            'start': piece_start,
            'end': piece_end,
            'text': text,
            'sponsor': SponsorService.extract_sponsor_from_text(text) or None,
        })
    return pieces
