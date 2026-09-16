"""Divider planning for splitting a merged multi-sponsor ad marker (issue #563).

One marker over back-to-back ads is right for the audio and wrong for pattern
learning: confirming it mints one oversized multi-sponsor template. These
helpers propose divider times from four sources, each mapped back to a
timestamp: AD_TRANSITION_PHRASES in the span's transcript, the member spans a
merge recorded, a clean handoff from one brand to another in the text, and
measured cut times the analyzer left on the marker. Free of Flask and DB
imports so it unit tests directly.
"""
from community_export import brand_match_candidates, get_sponsor_row_or_stub
from config import MIN_AD_DURATION
from sponsor_service import SponsorService
from text_pattern_matcher import AD_TRANSITION_PHRASES, find_transition_offsets
from utils.constants import MIN_BRAND_MATCH_CHARS
from utils.markers import DAI_CORE_SPANS, MERGED_MEMBER_SPANS, finite_number
from utils.text import pattern_offsets, word_boundary_re


def _join(spans: list[dict]) -> str:
    """Rebuild the text extract_timed_spans_in_range's offsets index into."""
    return ' '.join(span['text'] for span in spans)


def _time_at_offset(spans: list[dict], offset: int) -> float | None:
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


def _brand_row(brand) -> dict | None:
    """A sponsor-row shape for a registry row or a bare brand string."""
    if isinstance(brand, dict):
        return brand if (brand.get('name') or '').strip() else None
    name = str(brand).strip() if brand else ''
    return get_sponsor_row_or_stub(None, name) if name else None


def brand_mention_offsets(text: str, brands, compiled=None) -> dict[str, list[int]]:
    """Word-boundary offsets of each brand's mentions in `text`, by name.
    Brands absent from the text are left out, so len() is the count of distinct
    advertisers a span names. ``compiled`` is SponsorService's matcher cache by
    canonical name; a brand missing from it (a bare label the registry has no
    row for) is compiled here."""
    if not text or not brands:
        return {}
    patterns = {}
    for brand in brands:
        row = _brand_row(brand)
        if row is None:
            continue
        pattern = (compiled or {}).get(row['name']) or word_boundary_re(
            variant for variant in brand_match_candidates(row)
            if len(variant) >= MIN_BRAND_MATCH_CHARS)
        if pattern is not None:
            patterns[row['name']] = pattern
    return pattern_offsets(text, patterns)


def _brand_handoffs(text: str, brands, compiled=None) -> list[tuple[int, str]]:
    """Offsets where one brand's mentions stop and another's begin.
    Only a clean handoff counts: every earlier brand must be finished before the
    new one opens, or an interleaved pair would cut mid-read."""
    mentions = brand_mention_offsets(text, brands, compiled)
    if len(mentions) < 2:
        return []
    ordered = sorted(mentions.items(), key=lambda item: item[1][0])
    return [(offsets[0], name)
            for index, (name, offsets) in enumerate(ordered)
            if index and all(prior[-1] < offsets[0]
                             for _, prior in ordered[:index])]


def marker_split_sources(marker: dict) -> tuple[list[dict], list[float]]:
    """The member spans and measured cut times a marker records.
    Members come from the merge bookkeeping; cuts are the gaps between measured DAI
    core spans, which are splice-anchored and so name real interior boundaries."""
    members: list[dict] = []
    for raw in marker.get(MERGED_MEMBER_SPANS) or []:
        if not isinstance(raw, dict):
            continue
        lo, hi = finite_number(raw.get('start')), finite_number(raw.get('end'))
        if lo is not None and hi is not None and hi > lo:
            members.append({'start': lo, 'end': hi,
                            'sponsor': raw.get('sponsor')})
    members.sort(key=lambda member: member['start'])

    cores = []
    for raw in marker.get(DAI_CORE_SPANS) or []:
        if not isinstance(raw, dict):
            continue
        lo, hi = finite_number(raw.get('start')), finite_number(raw.get('end'))
        if lo is not None and hi is not None and hi > lo:
            cores.append(lo)
    return members, sorted(cores)[1:]


def build_split_candidates(spans: list[dict], start: float, end: float,
                           members: list[dict] = None, brands=None,
                           cuts: list[float] = None, compiled=None) -> list[dict]:
    """Proposed divider times inside (start, end), earliest first.

    A candidate is dropped when it would leave a piece shorter than
    MIN_AD_DURATION on either side, so the editor never opens already invalid.
    Returns [] when no source proposes a divider.
    """
    text = _join(spans) if spans else ''
    proposals: list[tuple[float | None, str]] = []
    if text:
        proposals += [(_time_at_offset(spans, offset),
                       _phrase_at_offset(text, offset))
                      for offset in find_transition_offsets(text)]
        proposals += [(_time_at_offset(spans, offset), name)
                      for offset, name in _brand_handoffs(text, brands, compiled)]
    # A member's start is where the ad before it ended, so only the members
    # after the first name an interior boundary.
    proposals += [(member['start'], member.get('sponsor') or 'merged ad')
                  for member in sorted(members or [],
                                       key=lambda m: m['start'])[1:]]
    proposals += [(cut, 'measured cut') for cut in cuts or []]

    out: list[dict] = []
    for time, phrase in sorted((p for p in proposals if p[0] is not None),
                               key=lambda p: p[0]):
        # A divider at the very start of the block marks the block's own
        # opening, not a boundary between two ads inside it.
        if time - start < MIN_AD_DURATION or end - time < MIN_AD_DURATION:
            continue
        if any(abs(time - c['time']) < MIN_AD_DURATION for c in out):
            continue
        out.append({'time': time, 'phrase': phrase})
    return out


def build_split_pieces(spans: list[dict], start: float, end: float,
                       times: list[float], brands=None, compiled=None) -> list[dict]:
    """The pieces `times` would produce, each with its text and sponsor guess.

    Boundary times outside (start, end) are ignored rather than rejected: this
    builds a preview, and validation of a submitted split lives in the
    corrections endpoint.
    """
    inner = sorted(t for t in times if start < t < end)
    bounds = [start] + inner + [end]
    # Scanned once over the whole span; each piece reads the hits in its own
    # character range, so a split into N pieces still costs one pass per brand.
    mentions = brand_mention_offsets(_join(spans), brands, compiled)
    pieces: list[dict] = []
    for i in range(len(bounds) - 1):
        piece_start, piece_end = bounds[i], bounds[i + 1]
        # Positive-measure overlap: a span that only touches a boundary
        # belongs to its neighbour, not both pieces.
        in_piece = [span for span in spans
                    if span['end'] > piece_start and span['start'] < piece_end]
        text = ' '.join(span['text'] for span in in_piece)
        lo = in_piece[0]['offset'] if in_piece else 0
        hi = (in_piece[-1]['offset'] + len(in_piece[-1]['text'])) if in_piece else 0
        # A piece naming exactly one known brand is that brand's read; two
        # names leave it ambiguous, so the generic extractor answers instead.
        named = [name for name, offsets in mentions.items()
                 if any(lo <= offset < hi for offset in offsets)]
        pieces.append({
            'start': piece_start,
            'end': piece_end,
            'text': text,
            'sponsor': (named[0] if len(named) == 1
                        else SponsorService.extract_sponsor_from_text(text) or None),
        })
    return pieces
