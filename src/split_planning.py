"""Divider planning for splitting a merged multi-sponsor ad marker (issue #563).

One marker over back-to-back ads is right for the audio and wrong for pattern
learning: confirming it mints one oversized multi-sponsor template. These
helpers propose divider times from four sources, each mapped back to a
timestamp: AD_TRANSITION_PHRASES in the span's transcript, the member spans a
merge recorded, a clean handoff from one brand to another in the text, and
measured cut times the analyzer left on the marker. Free of Flask and DB
imports so it unit tests directly.
"""
import re

from community_export import brand_match_candidates, get_sponsor_row_or_stub
from config import MIN_AD_DURATION
from sponsor_service import SponsorService
from text_pattern_matcher import AD_TRANSITION_PHRASES, find_transition_offsets
from utils.constants import MIN_BRAND_MATCH_CHARS
from utils.markers import DAI_CORE_SPANS, MERGED_MEMBER_SPANS, finite_number
from utils.text import pattern_offsets, word_boundary_re

# A host opening a new read: "Hey, this is Sam from ...", "I'm Sam and ...", or a sponsor credit.
# "thanks to" also closes a read ("thanks to Acme for supporting the show"), so it opens none here.
HANDOFF_RE = re.compile(
    r"^\W*(?:(?:hey|hi|hello)\W+)?this is\s+(?-i:[A-Z][\w.'-]*)(?:\s+(?-i:[A-Z][\w.'-]*)){0,2}"
    r"\s+from\b|^\W*(?:" + '|'.join(
        re.escape(p) for p in AD_TRANSITION_PHRASES if p != 'thanks to') + ")",
    re.IGNORECASE)
# How far a measured cut or a transcript divider may move to reach a Whisper segment boundary.
DAI_SNAP_S = 5.0
SEGMENT_SNAP_S = 3.0
# A measured cut this close to a segment boundary is one the transcript confirms.
CUT_AGREES_S = 0.5


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


def _brand_handoffs(text: str, brands, compiled=None) -> list[tuple[int, str, int]]:
    """(offset, name, previous brand's last offset) where every earlier brand has finished and another
    begins, so an interleaved pair never cuts mid-read; a brand named once is a passing mention."""
    mentions = {name: offsets for name, offsets in
                brand_mention_offsets(text, brands, compiled).items() if len(offsets) >= 2}
    if len(mentions) < 2:
        return []
    ordered = sorted(mentions.items(), key=lambda item: item[1][0])
    return [(offsets[0], name, max(prior[-1] for _, prior in ordered[:index]))
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
                            'sponsor': raw.get('sponsor'),
                            'category': raw.get('category')})
    members.sort(key=lambda member: member['start'])

    cores = []
    for raw in marker.get(DAI_CORE_SPANS) or []:
        if not isinstance(raw, dict):
            continue
        lo, hi = finite_number(raw.get('start')), finite_number(raw.get('end'))
        if lo is not None and hi is not None and hi > lo:
            cores.append(lo)
    return members, sorted(cores)[1:]


def _segment_snapper(segments, start: float, end: float):
    """Helpers mapping a time onto the Whisper segment boundaries inside (start, end)."""
    inside = sorted((seg for seg in segments or []
                     if seg.get('end', 0.0) > start and seg.get('start', 0.0) < end),
                    key=lambda seg: seg.get('start', 0.0))
    bounds = sorted({seg.get('start', 0.0) for seg in inside if start < seg.get('start', 0.0) < end})

    def near(time, tol):
        best = min(bounds, key=lambda b: abs(b - time), default=None)
        return best if best is not None and abs(best - time) <= tol else None

    def holder(time):
        return next((seg for seg in inside
                     if seg.get('start', 0.0) < time < seg.get('end', 0.0)), None)

    return inside, near, holder


def build_split_candidates(spans: list[dict], start: float, end: float,
                           members: list[dict] = None, brands=None,
                           cuts: list[float] = None, compiled=None,
                           segments: list[dict] = None) -> list[dict]:
    """Proposed divider times inside (start, end), earliest first.

    A candidate is dropped when it would leave a piece shorter than
    MIN_AD_DURATION on either side, so the editor never opens already invalid.
    Returns [] when no source proposes a divider. `segments` are the Whisper
    segments behind word-level `spans`: dividers move onto their boundaries.
    """
    text = _join(spans) if spans else ''
    inside, near, holder = _segment_snapper(segments, start, end)

    def on_boundary(time, tol=SEGMENT_SNAP_S):
        # A divider never splits a Whisper segment when a boundary lies close by.
        snapped = near(time, tol) if time is not None and segments else None
        return snapped if snapped is not None else time

    proposals: list[tuple[float | None, str]] = []
    if text:
        proposals += [(on_boundary(_time_at_offset(spans, offset)),
                       _phrase_at_offset(text, offset))
                      for offset in find_transition_offsets(text)]
    # A segment opening with a host handoff starts a new read.
    handoffs = [seg.get('start', 0.0) for seg in inside
                if start < seg.get('start', 0.0) < end and HANDOFF_RE.match(seg.get('text') or '')]
    proposals += [(time, 'handoff') for time in handoffs]
    if text:
        for offset, name, previous in _brand_handoffs(text, brands, compiled):
            time = _time_at_offset(spans, offset)
            prior = _time_at_offset(spans, previous)
            if segments and time is not None and prior is not None:
                if any(prior < h <= time for h in handoffs):
                    continue  # The host handoff already divides the two reads.
                # The new read opens with the first segment after the old brand's last mention.
                after = next((seg.get('end', 0.0) for seg in inside
                              if seg.get('start', 0.0) <= prior < seg.get('end', 0.0)), prior)
                time = next((seg.get('start', 0.0) for seg in inside
                             if after - 0.01 <= seg.get('start', 0.0) <= time), time)
            proposals.append((on_boundary(time), name))
    # A member's start is where the ad before it ended, so only the members
    # after the first name an interior boundary; one nested in an earlier member names none.
    reach = None
    for member in sorted(members or [], key=lambda m: m['start']):
        if reach is not None and member['end'] > reach:
            proposals.append((member['start'], member.get('sponsor') or 'merged ad'))
        reach = member['end'] if reach is None else max(reach, member['end'])
    mention_times = [sorted(t for t in (_time_at_offset(spans, o) for o in offsets) if t is not None)
                     for offsets in brand_mention_offsets(text, brands, compiled).values()
                     ] if text and cuts else []
    for cut in cuts or []:
        # A cut the transcript agrees with stands; one between two mentions of a brand is inside its read.
        agreed = bool(segments) and near(cut, CUT_AGREES_S) is not None
        if not agreed and any(times and times[0] < cut < times[-1] for times in mention_times):
            continue
        if segments:
            # A measured cut gives way to a nearby transcript boundary, and never cuts through speech.
            snapped = near(cut, DAI_SNAP_S)
            if snapped is None and holder(cut) is not None:
                continue
            cut = snapped if snapped is not None else cut
        proposals.append((cut, 'measured cut'))

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
                       times: list[float], brands=None, compiled=None,
                       sponsor: str = None) -> list[dict]:
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
        # A piece naming one brand, or repeating only one, is that brand's read; two
        # repeated brands are two reads, so the generic extractor answers.
        named = {name: hits for name, offsets in mentions.items()
                 if (hits := [o for o in offsets if lo <= o < hi])}
        repeated = {name: hits for name, hits in named.items() if len(hits) >= 2}
        # A single hit is a passing mention unless it is the caller's own sponsor.
        lone = next(iter(named)) if len(named) == 1 else None
        brand = (lone if lone and (len(named[lone]) >= 2 or (sponsor and lone.lower() == sponsor.lower()))
                 else next(iter(repeated)) if len(repeated) == 1 else None)
        pieces.append({
            'start': piece_start,
            'end': piece_end,
            'text': text,
            'sponsor': brand or SponsorService.extract_sponsor_from_text(text) or None,
        })
    return pieces
