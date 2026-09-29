"""Marker-dict bookkeeping shared by the detector, validator, and reviewer."""
import hashlib
import json
import math
import uuid

from config import (CORRECTION_MATCH_MIN_COVERAGE, FINGERPRINT_CHUNK_SIZE,
                    PASS2_REVIEWED_RELEASE_HOLD_REASONS, REVIEWER_HOLD_REASONS,
                    is_pending_review, repair_segment_category)
from utils.time import overlap_ratio


# DAI core: the region measured as differing across fetches; it answers whether the audio is an ad.
# DAI probes: sub-windows where correlation was computed; they answer whether an edge is measured.
# Read both only through dai_core_spans/dai_core_bounds and dai_probe_spans.
DAI_CORE_SPANS = 'dai_core_spans'
DAI_PROBE_SPANS = 'dai_probe_spans'
# Probe geometry shared with differential_fetcher._probe_block.
DAI_PROBE_LEAD_S = 0.5
DAI_PROBE_REF_S = 4.0

EDGE_TOLERANCE = 0.05
COVERAGE_GAP_TOLERANCE = 3.0


def invalidate_tail_provenance(marker: dict, new_end: float) -> None:
    """Drop tail-growth eligibility when a stage selects a new end.

    Content-tail provenance describes how the current edge was reached. A
    reviewer or human-selected end must earn any later sonic-tail extension
    from fresh evidence instead of reusing stale eligibility.
    """
    if marker.get('end') != new_end:
        marker.pop('end_extended_by_content', None)
        marker.pop('tail_splice_snap', None)


def finite_number(value) -> float | None:
    """Value as a finite float, or None when it is not one."""
    if isinstance(value, bool):  # float(True) == 1.0, not a timestamp
        return None
    try:
        number = float(value)
    except (OverflowError, TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def quote_edge_valid(marker: dict, edge: str) -> bool:
    quote_time = finite_number(marker.get(f'quote_{edge}'))
    edge_time = finite_number(marker.get(edge))
    return bool(marker.get(f'quote_aligned_{edge}')
                and quote_time is not None and edge_time is not None
                and abs(quote_time - edge_time) <= EDGE_TOLERANCE)


def invalidate_quote_alignment(marker: dict) -> None:
    for edge in ('start', 'end'):
        if marker.get(f'quote_aligned_{edge}') and not quote_edge_valid(marker, edge):
            marker.pop(f'quote_aligned_{edge}', None)
            marker.pop(f'quote_{edge}', None)


def word_timed_edge_valid(marker: dict, edge: str) -> bool:
    timed = finite_number(marker.get(f'word_timed_{edge}'))
    current = finite_number(marker.get(edge))
    return timed is not None and current is not None and abs(timed - current) <= EDGE_TOLERANCE


def inherit_edge(target: dict, source: dict, edge: str) -> None:
    """Move target's edge to source's, taking source's valid quote and word-timed provenance."""
    if quote_edge_valid(source, edge):
        target[f'quote_aligned_{edge}'] = source[f'quote_aligned_{edge}']
        target[f'quote_{edge}'] = source[f'quote_{edge}']
    if word_timed_edge_valid(source, edge):
        target[f'word_timed_{edge}'] = source[f'word_timed_{edge}']
    target[edge] = source[edge]


def invalidate_word_timed_edges(marker: dict) -> None:
    for edge in ('start', 'end'):
        if (f'word_timed_{edge}' in marker
                and not word_timed_edge_valid(marker, edge)):
            marker.pop(f'word_timed_{edge}', None)


def is_reviewer_rejected(marker: dict) -> bool:
    """Whether the reviewer rejected this marker outright (no hold)."""
    # was_cut is ignored so a reject wrongly saved as cut repairs on the next recut.
    return (marker.get('reviewer_verdict') == 'reject'
            and marker.get('source') == 'reviewer'
            and not marker.get('held_for_review'))


def covering_confirm(start, end, confirmed, *, where=None,
                     prefer_confirmed_span=False) -> dict | None:
    """First (newest) confirm covering >= CORRECTION_MATCH_MIN_COVERAGE of the range.

    Tests the original and confirmed_span; prefer_confirmed_span tests only the
    confirmed_span when there is one, so a stale wide original cannot match.
    """
    if start is None or end is None or end - start < 0.001:
        return None
    for corr in confirmed or []:
        if where is not None and not where(corr):
            continue
        span = corr.get('confirmed_span')
        for s in ((span or corr,) if prefer_confirmed_span else (corr, span)):
            if s and overlap_ratio(s['start'], s['end'], start, end) >= CORRECTION_MATCH_MIN_COVERAGE:
                return corr
    return None


def auto_confirm_releases(corr: dict, hold_reason) -> bool:
    """Whether an auto-filed confirm releases a hold: only one filed for the same hold reason."""
    return bool(corr.get('auto_filed')) and corr.get('hold_reason') == hold_reason


def explicit_override(marker: dict, confirmed: list[dict]) -> bool:
    """Whether a user (not auto-filed) confirm or boundary adjustment covers the marker."""
    return covering_confirm(marker['start'], marker['end'], confirmed,
                            where=lambda c: not c.get('auto_filed')) is not None


def reviewer_reject_stands(marker: dict, confirmed: list[dict]) -> bool:
    """Whether a reviewer reject survives: only a user confirm or adjustment lifts it."""
    return is_reviewer_rejected(marker) and not explicit_override(marker, confirmed)


def reject_barriers(rejects: list[dict], confirmed: list[dict]) -> list[dict]:
    """Reviewer-reject spans minus every user (not auto-filed) confirm or adjustment interval."""
    user = [(c.get('confirmed_span') or c) for c in confirmed or [] if not c.get('auto_filed')]
    return [{'start': lo, 'end': hi} for r in rejects or []
            for lo, hi in subtract_spans([(r['start'], r['end'])],
                                         [(s['start'], s['end']) for s in user])]


def reviewer_hold_stands(marker: dict, confirmed: list[dict]) -> bool:
    """Whether a pending reviewer hold survives a recut: no user override, no pass-2 release."""
    reason = marker.get('hold_reason')
    if (not marker.get('held_for_review') or marker.get('was_cut')
            or reason not in REVIEWER_HOLD_REASONS or explicit_override(marker, confirmed)):
        return False
    return not (reason in PASS2_REVIEWED_RELEASE_HOLD_REASONS and covering_confirm(
        marker['start'], marker['end'], confirmed,
        where=lambda c: auto_confirm_releases(c, reason)) is not None)


def reviewer_edge_locked(marker: dict, edge: str) -> bool:
    """Whether a reviewer-validated bound forbids widening this edge."""
    locked = finite_number(marker.get(f'reviewer_locked_{edge}'))
    current = finite_number(marker.get(edge))
    if locked is None or current is None:
        return False
    if edge == 'start':
        return current >= locked - EDGE_TOLERANCE
    return current <= locked + EDGE_TOLERANCE


def precise_edge(marker: dict, edge: str) -> bool:
    """Whether a quote, word timing, or reviewer lock pins this edge."""
    return (quote_edge_valid(marker, edge) or word_timed_edge_valid(marker, edge)
            or reviewer_edge_locked(marker, edge))


def set_reviewer_locks(marker: dict, edges) -> None:
    """Lock the named edges at their current values and clear the others."""
    for edge in ('start', 'end'):
        marker.pop(f'reviewer_locked_{edge}', None)
    for edge in edges:
        marker[f'reviewer_locked_{edge}'] = marker[edge]


def drop_stale_reviewer_locks(marker: dict) -> None:
    """Drop a reviewer lock whose edge a merge has widened past."""
    for edge in ('start', 'end'):
        if f'reviewer_locked_{edge}' in marker and not reviewer_edge_locked(marker, edge):
            marker.pop(f'reviewer_locked_{edge}', None)


# Both-edges tolerance for treating two markers as the same span. Matches
# find_marker_in_list, the reject path, and the review listing, so every
# consumer agrees on what one span means.
BOUNDS_TOLERANCE_S = 0.5


def spans_match(a_start, a_end, b_start, b_end,
                tol: float = BOUNDS_TOLERANCE_S) -> bool:
    """Both edges within tol seconds."""
    if a_start is None or a_end is None or b_start is None or b_end is None:
        return False
    return abs(a_start - b_start) <= tol and abs(a_end - b_end) <= tol


def find_marker_in_list(markers, start, end, tol: float = BOUNDS_TOLERANCE_S):
    """Bounds match within tolerance against an already-loaded marker list."""
    for marker in markers or []:
        if spans_match(marker.get('start'), marker.get('end'), start, end, tol):
            return marker
    return None


def _valid_spans(marker: dict, key: str, extra_field: str | None = None,
                 optional_fields: tuple = ()) -> list[dict]:
    """Well-formed {start, end} spans under `key`, plus extra and present optional fields."""
    raw_spans = marker.get(key)
    if not isinstance(raw_spans, list):
        return []
    spans = []
    for raw in raw_spans:
        if not isinstance(raw, dict):
            continue
        start = finite_number(raw.get('start'))
        end = finite_number(raw.get('end'))
        if start is not None and end is not None and end > start:
            span = {'start': start, 'end': end}
            if extra_field is not None:
                span[extra_field] = raw.get(extra_field)
            span.update((f, raw[f]) for f in optional_fields if f in raw)
            spans.append(span)
    return spans


def _clip_spans(marker: dict, key: str, spans: list[dict], start: float,
                end: float, keep_empty: bool = False) -> None:
    """Store `spans` under `key` clipped into [start, end], dropping collapsed
    ones; `keep_empty` writes an empty list instead of removing the key."""
    if keep_empty and key not in marker:
        return
    clipped = []
    for span in spans:
        lo = max(start, span['start'])
        hi = min(end, span['end'])
        if hi > lo:
            span['start'], span['end'] = lo, hi
            clipped.append(span)
    if clipped or keep_empty:
        marker[key] = clipped
    else:
        marker.pop(key, None)


def _valid_dai_core_spans(marker: dict) -> list[dict[str, float]]:
    """Normalized measured DAI spans from a marker."""
    return _valid_spans(marker, DAI_CORE_SPANS)


def dai_probe_window(start: float, end: float) -> tuple[float, float]:
    """Window the fetcher correlates inside one differential block."""
    ref = min(DAI_PROBE_REF_S, end - start)
    lead = start + min(DAI_PROBE_LEAD_S, (end - start) - ref)
    return lead, lead + ref


def _normalize_legacy_probes(marker: dict) -> None:
    """Record probe spans on a marker saved before DAI probes existed: each core span's leading window."""
    if isinstance(marker.get(DAI_PROBE_SPANS), list):
        return
    probes = [{'start': s['start'],
               'end': min(s['end'], s['start'] + DAI_PROBE_LEAD_S
                          + min(DAI_PROBE_REF_S, s['end'] - s['start']))}
              for s in _valid_dai_core_spans(marker)]
    if probes:
        marker[DAI_PROBE_SPANS] = probes


def _expand_legacy_partial_cut(marker: dict) -> list[dict]:
    """Carved fragments for a marker saved with partial_cut_spans; [marker] otherwise."""
    covered = [(s['start'], s['end']) for s in _valid_spans(marker, 'partial_cut_spans')]
    marker.pop('partial_cut_spans', None)
    start, end = finite_number(marker.get('start')), finite_number(marker.get('end'))
    # A pending marker stays whole: carving would multiply pending counts and holds.
    if (marker.get('was_cut') or is_pending_review(marker) or not covered
            or start is None or end is None):
        return [marker]
    return carve_partly_cut(marker, start, end, covered) or [marker]


def ensure_hold_id(marker: dict) -> None:
    """Give a held marker a stable identity once; copies and carved fragments keep it."""
    if marker.get('held_for_review') and not marker.get('hold_id'):
        marker['hold_id'] = uuid.uuid4().hex[:12]


def _legacy_hold_id(marker: dict) -> str:
    """Derive a hold id from bounds and reason so repeated loads agree until it is saved."""
    key = f"{marker.get('start') or 0.0:.2f}-{marker.get('end') or 0.0:.2f}-{marker.get('hold_reason')}"
    return hashlib.sha1(key.encode(), usedforsecurity=False).hexdigest()[:12]


def normalize_loaded_markers(markers: list) -> list:
    """Bring persisted markers up to the current shape in place; returns the list."""
    expanded = []
    for marker in markers:
        if (isinstance(marker, dict) and marker.get('held_for_review')
                and not marker.get('hold_id')):
            marker['hold_id'] = _legacy_hold_id(marker)
        if not isinstance(marker, dict) or (
                DAI_CORE_SPANS not in marker and 'partial_cut_spans' not in marker):
            expanded.append(marker)
            continue
        _normalize_legacy_probes(marker)
        expanded.extend(_expand_legacy_partial_cut(marker))
    markers[:] = expanded
    return markers


def parse_ad_markers(raw) -> list[dict] | None:
    """Parsed and normalized ad_markers_json; None when absent, unreadable or not a list."""
    if not raw:
        return None
    try:
        markers = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(markers, list):
        return None
    return normalize_loaded_markers(markers)


def dai_core_spans(marker: dict) -> list[tuple[float, float]]:
    """(start, end) of a marker's measured DAI regions."""
    return [(s['start'], s['end']) for s in _valid_dai_core_spans(marker)]


def dai_probe_spans(marker: dict) -> list[tuple[float, float]]:
    """(start, end) windows of a marker's DAI regions that were measured."""
    return [(s['start'], s['end']) for s in _valid_spans(marker, DAI_PROBE_SPANS)]


def merge_dai_core_spans(target: dict, other: dict) -> None:
    """Carry measured DAI regions through a marker merge."""
    spans = _valid_dai_core_spans(target) + _valid_dai_core_spans(other)
    if not spans:
        return
    probes = _valid_spans(target, DAI_PROBE_SPANS) + _valid_spans(other, DAI_PROBE_SPANS)
    target[DAI_CORE_SPANS] = [{'start': lo, 'end': hi} for lo, hi in merge_runs(
        [(s['start'], s['end']) for s in spans], gap=EDGE_TOLERANCE)]
    target[DAI_PROBE_SPANS] = sorted(probes, key=lambda span: span['start'])


def clip_dai_core_spans(marker: dict, start: float, end: float) -> None:
    """Clip a marker's DAI evidence to a newly split/clamped range."""
    core = _valid_dai_core_spans(marker)
    _clip_spans(marker, DAI_CORE_SPANS, core, start, end)
    if DAI_CORE_SPANS in marker:
        _clip_spans(marker, DAI_PROBE_SPANS, _valid_spans(marker, DAI_PROBE_SPANS),
                    start, end, keep_empty=True)
    else:
        marker.pop(DAI_PROBE_SPANS, None)


def span_bounds(spans) -> tuple[float | None, float | None]:
    """Outer bounds of a span list, None/None when it holds nothing."""
    if not spans:
        return None, None
    return min(s['start'] for s in spans), max(s['end'] for s in spans)


def dai_core_bounds(marker: dict) -> tuple[float | None, float | None]:
    """Return the outer bounds of measured DAI evidence, if present."""
    return span_bounds(_valid_dai_core_spans(marker))

# Stages whose spans carry alignment-derived padding (especially tails)
# rather than transcript- or splice-anchored bounds. Members from these
# stages are trimmable by the reviewer; every other stage's span is
# protected inside a merge, except that a duration-estimated text-pattern
# span protects only its matched text. Blacklist (not whitelist) so a future
# stage name fails conservative: unknown stages are protected.
UNPROTECTED_MEMBER_STAGES = frozenset({'dai_differential', 'vad_gap'})

# LLM- or heuristic-derived edges carry padding the reviewer may trim, so a
# proposal only has to keep overlapping such a member. Every other stage is
# measured (fingerprint, cue_pair, text_pattern, manual) and must stay covered,
# a duration-estimated text-pattern span only over its matched text.
COARSE_MEMBER_STAGES = frozenset({
    'claude', 'first_pass', 'verification', 'verification_miss',
    'heuristic_preroll', 'heuristic_postroll', 'language',
    # Complement of an LLM content label, so its edges are as coarse as the
    # label's.
    'keep_content',
})

MERGED_MEMBER_SPANS = 'merged_member_spans'

# Per-member evidence recorded at merge time, before merges move the edges.
_MEMBER_FIELDS = ('confidence', 'precise_start', 'precise_end',
                  'fingerprint_match_start', 'fingerprint_match_end',
                  'span_estimated', 'pattern_id', 'sponsor', 'category')

# Stages measured from audio or matched transcript text, not proposed by a model.
# dai_differential is not one: a cross-fetch diff earns KeepDifferentialOverride
# but never outranks a reviewer reject by itself.
MEASURED_EVIDENCE_STAGES = frozenset({
    'fingerprint', 'cue_pair', 'text_pattern', 'manual',
})

# Merge bookkeeping a split fragment must drop: it describes the merged
# span, not the narrower piece the split just carved out.
_MERGE_BOOKKEEPING_KEYS = ('merged_distinct_ads', 'merged_protected_start',
                           'merged_protected_end', MERGED_MEMBER_SPANS)


def _protected_bounds(marker: dict) -> tuple[float | None, float | None]:
    """Protected span one merge member contributes: its own recorded union
    when it was merged before, its span when its stage is anchored, else
    None/None."""
    if 'merged_protected_start' in marker:
        return (marker['merged_protected_start'],
                marker.get('merged_protected_end'))
    if marker.get('detection_stage') not in UNPROTECTED_MEMBER_STAGES:
        return marker['start'], marker['end']
    return None, None


def estimated_text_bounds(marker: dict) -> tuple[float, float] | None:
    """Matched-text bounds of a duration-estimated span, clipped to the span."""
    if not marker.get('span_estimated'):
        return None
    lo = finite_number(marker.get('text_start'))
    hi = finite_number(marker.get('text_end'))
    span_start = finite_number(marker.get('start'))
    span_end = finite_number(marker.get('end'))
    if lo is None or hi is None or span_start is None or span_end is None:
        return None
    lo = max(lo, span_start)
    # Text recorded past the span claims no audio rather than an inverted range.
    return lo, max(lo, min(hi, span_end))


def recorded_member_spans(marker: dict) -> list[dict]:
    """Normalized member spans recorded on a merged marker."""
    return _valid_spans(marker, MERGED_MEMBER_SPANS, 'stage', _MEMBER_FIELDS)


def protected_member_spans(marker: dict, fallback_start=None,
                           fallback_end=None) -> list[dict]:
    """Member-shaped spans a merged marker protects, legacy markers included.

    A marker persisted before member tracking knows only its recorded union,
    or the caller's original bounds; that becomes one stage-less member, and
    an unknown stage counts as measured, so it stays expand-only.
    """
    members = recorded_member_spans(marker)
    if members:
        return members
    if 'merged_protected_start' in marker:
        start = marker.get('merged_protected_start')
        end = marker.get('merged_protected_end')
    else:
        start, end = fallback_start, fallback_end
    if start is None or end is None:
        return []
    return [{'start': start, 'end': end, 'stage': None}]


def clip_member_spans(marker: dict, start: float, end: float) -> None:
    """Clip recorded member spans to a range, dropping collapsed members."""
    spans = recorded_member_spans(marker)
    for span in spans:
        # A moved edge is no longer the one that was measured.
        for edge, moved in (('start', span['start'] < start),
                            ('end', span['end'] > end)):
            if moved and f'precise_{edge}' in span:
                span[f'precise_{edge}'] = False
    # keep_empty: the key's presence is what marks a tracked merge.
    _clip_spans(marker, MERGED_MEMBER_SPANS, spans, start, end, keep_empty=True)


def _clip_match(record: dict, lo: float, hi: float) -> bool:
    """Clip a record's fingerprint match into [lo, hi]; False when none of it is left."""
    match = _match_bounds(record)
    if match is None:
        return True
    match_lo, match_hi = max(match[0], lo), min(match[1], hi)
    if match_hi <= match_lo:
        record.pop('fingerprint_match_start', None)
        record.pop('fingerprint_match_end', None)
        return False
    record['fingerprint_match_start'], record['fingerprint_match_end'] = match_lo, match_hi
    return True


def clip_merge_spans(marker: dict, lo: float, hi: float) -> None:
    """Clamp every merge record and measured provenance a marker carries into [lo, hi]."""
    for key in ('merged_protected_start', 'merged_protected_end'):
        bound = marker.get(key)
        if bound is not None:
            marker[key] = min(max(bound, lo), hi)
    clip_member_spans(marker, lo, hi)
    if isinstance(marker.get(MERGED_MEMBER_SPANS), list):
        # A fingerprint member with no match left in range proves nothing here.
        marker[MERGED_MEMBER_SPANS] = [m for m in marker[MERGED_MEMBER_SPANS]
                                       if _clip_match(m, lo, hi)]
    _clip_match(marker, lo, hi)
    clip_dai_core_spans(marker, lo, hi)


def member_spans(marker: dict) -> list[dict]:
    """Member spans one merge member contributes to the target's list."""
    if 'merged_protected_start' in marker:
        if isinstance(marker.get(MERGED_MEMBER_SPANS), list):
            return recorded_member_spans(marker)
        # Tracked by a release that recorded only the union: all it knows is
        # one span, and an unknown stage stays measured.
        lo = marker['merged_protected_start']
        hi = marker.get('merged_protected_end')
        if lo is None or hi is None:
            return []
        return [{'start': lo, 'end': hi, 'stage': None}]
    lo, hi = _protected_bounds(marker)
    if lo is None or hi is None:
        return []
    if marker.get('span_estimated'):
        # An estimate protects only its matched text; without any it carries no evidence.
        text = estimated_text_bounds(marker)
        if text is None or text[1] <= text[0]:
            return []
        lo, hi = text
    stage = marker.get('detection_stage')
    member = {'start': lo, 'end': hi, 'stage': stage}
    if marker.get('span_estimated') and not marker.get('pattern_defined'):
        member['span_estimated'] = True
    # Labels let pattern learning tell a self-promo intro from the sponsor read after it.
    sponsor = marker.get('sponsor')
    if isinstance(sponsor, str) and sponsor.strip():
        member['sponsor'] = sponsor.strip()
    category = repair_segment_category(marker.get('category'))
    if category:
        member['category'] = category
    if stage in COARSE_MEMBER_STAGES:
        confidence = finite_number(marker.get('confidence'))
        if confidence is not None:
            member['confidence'] = confidence
        for edge in ('start', 'end'):
            member[f'precise_{edge}'] = (quote_edge_valid(marker, edge)
                                         or word_timed_edge_valid(marker, edge))
    elif stage == 'fingerprint':
        match_lo = finite_number(marker.get('fingerprint_match_start'))
        match_hi = finite_number(marker.get('fingerprint_match_end'))
        if match_lo is not None and match_hi is not None:
            member['fingerprint_match_start'] = match_lo
            member['fingerprint_match_end'] = match_hi
        if marker.get('pattern_id') is not None:
            member['pattern_id'] = marker['pattern_id']
    return [member]


def _take_coarse_edge(prior: dict, span: dict, edge: str, pick) -> None:
    """Widen prior's edge, taking the precise flag from the member that supplies it."""
    key = f'precise_{edge}'
    if span[edge] == prior[edge]:
        flag = bool(prior.get(key) or span.get(key))
    elif pick(prior[edge], span[edge]) == span[edge]:
        prior[edge] = span[edge]
        flag = bool(span.get(key))
    else:
        return
    if key in prior or key in span:
        prior[key] = flag


def member_label(member: dict) -> tuple[str | None, str | None]:
    """(lowercased sponsor, category) a member was recorded with."""
    return ((member.get('sponsor') or '').strip().lower() or None,
            member.get('category') or None)


def labels_compatible(a: tuple, b: tuple) -> bool:
    """Whether two member labels agree; a missing component matches anything."""
    return all(x is None or y is None or x == y for x, y in zip(a, b, strict=True))


def _coalesce_coarse_members(spans: list[dict]) -> list[dict]:
    """Union overlapping same-stage, compatibly labeled coarse members: two LLM windows over one ad are one member."""
    merged: list[dict] = []
    for span in spans:
        stage = span.get('stage')
        prior = next(
            (m for m in merged if m.get('stage') == stage
             and stage in COARSE_MEMBER_STAGES
             and labels_compatible(member_label(m), member_label(span))
             and span['start'] <= m['end'] and span['end'] >= m['start']),
            None) if stage in COARSE_MEMBER_STAGES else None
        if prior is None:
            merged.append(span)
            continue
        for key in ('sponsor', 'category'):
            if not prior.get(key) and span.get(key):
                prior[key] = span[key]
        _take_coarse_edge(prior, span, 'start', min)
        _take_coarse_edge(prior, span, 'end', max)
        # The weakest member bounds what the coalesced span proves; unknown is weakest.
        confidences = (finite_number(prior.get('confidence')),
                       finite_number(span.get('confidence')))
        if None in confidences:
            prior.pop('confidence', None)
        else:
            prior['confidence'] = min(confidences)
    return merged


def _widen(prior, new, pick):
    """The wider of two bounds, or whichever one of them is set."""
    if prior is None:
        return new
    if new is None:
        return prior
    return pick(prior, new)


def note_merged_members(target: dict, other: dict) -> None:
    """Record the protected members on any merge, distinct or overlapping.

    Call BEFORE the merge mutates target's span or stage. Always writes
    merged_protected_start/end and merged_member_spans on target (None/None
    and [] when no member is anchored) so the reviewer can tell a tracked
    merge from a legacy marker persisted by a pre-tracking release.
    """
    if other.get('has_estimated_pattern_member'):
        target['has_estimated_pattern_member'] = True
    merge_dai_core_spans(target, other)
    spans = _coalesce_coarse_members(member_spans(target) + member_spans(other))
    target[MERGED_MEMBER_SPANS] = spans
    # A union already on the target only widens: a legacy bound has no member
    # span backing it.
    lo, hi = span_bounds(spans)
    target['merged_protected_start'] = _widen(
        target.get('merged_protected_start'), lo, min)
    target['merged_protected_end'] = _widen(
        target.get('merged_protected_end'), hi, max)


def measured_member_spans(marker: dict, min_conf: float, *,
                          include_dai_core: bool = True) -> list[tuple[float, float, bool]]:
    """Measured (start, end, is_anchor) spans of a marker, sorted by start."""
    # Anchors are independent member evidence: no DAI core, no estimate's own text.
    spans = ([(s['start'], s['end'], False) for s in _valid_dai_core_spans(marker)]
             if include_dai_core else [])
    # Same hard extents the reviewer uses: a fingerprint start is measured, its projected end soft.
    spans.extend((m['start'], m['end'], not m.get('span_estimated'))
                 for m in hard_members(marker, min_conf) if _is_evidence(m, min_conf))
    return sorted(spans)


def _transcript_member(member: dict, min_conf: float) -> bool:
    """Whether a member is a confident LLM or heuristic span that can carry a precise edge."""
    confidence = finite_number(member.get('confidence'))
    return (member.get('stage') in COARSE_MEMBER_STAGES
            and member.get('stage') != 'keep_content'
            and confidence is not None and confidence >= min_conf)


def _is_evidence(member: dict, min_conf: float) -> bool:
    """A confident transcript member, or a measured member that is not an unmatched fingerprint."""
    return _transcript_member(member, min_conf) or (
        member.get('stage') in MEASURED_EVIDENCE_STAGES and not _unmatched_fingerprint(member))


def _match_bounds(member: dict) -> tuple[float, float] | None:
    """A member's recorded fingerprint match, or None when it has none."""
    match_lo = finite_number(member.get('fingerprint_match_start'))
    match_hi = finite_number(member.get('fingerprint_match_end'))
    return None if match_lo is None or match_hi is None else (match_lo, match_hi)


def _unmatched_fingerprint(member: dict) -> bool:
    return member.get('stage') == 'fingerprint' and _match_bounds(member) is None


def _hard_extent(member: dict, precise: list[dict]) -> tuple[float, float] | None:
    """A member's measured extent given the precise transcript members beside it."""
    lo, hi = member['start'], member['end']
    stage = member.get('stage')
    match = _match_bounds(member)
    if stage == 'fingerprint' and match is not None:
        # Only the first correlation window is measured; the rest is a projected
        # pattern length, soft past the latest precise transcript end.
        lo, hi = max(lo, match[0]), min(hi, match[1])
        ends = [m['end'] for m in precise if m.get('precise_end')]
        if ends:
            hi = max(min(hi, max(ends)), min(hi, lo + FINGERPRINT_CHUNK_SIZE))
    elif stage == 'text_pattern':
        # Segment-bounded edges are soft only past a precise edge that falls inside them.
        starts = [m['start'] for m in precise if m.get('precise_start') and lo < m['start'] < hi]
        ends = [m['end'] for m in precise if m.get('precise_end') and lo < m['end'] < hi]
        lo = min(starts, default=lo)
        hi = max(ends, default=hi)
    return (lo, hi) if hi > lo else None


def _hard_members(members: list[dict], min_conf: float) -> list[dict]:
    """Members clipped to their measured extent, empty ones dropped."""
    precise = [m for m in members if _transcript_member(m, min_conf)]
    hard = []
    for member in members:
        extent = _hard_extent(member, precise)
        if extent is not None:
            hard.append({**member, 'start': extent[0], 'end': extent[1]})
    return hard


def hard_member_spans(ad: dict, lo, hi, min_conf: float) -> list[dict]:
    """protected_member_spans with every member clipped to what it measured."""
    return _hard_members(protected_member_spans(ad, lo, hi), min_conf)


def hard_members(ad: dict, min_conf: float) -> list[dict]:
    """member_spans clipped to what each member measured."""
    return _hard_members(member_spans(ad), min_conf)


def edge_support(ad: dict, edge: str, min_conf: float, hard=None) -> dict:
    """The envelope edge and the outermost member edge that measures it."""
    candidates = []
    for member in hard_members(ad, min_conf) if hard is None else hard:
        if not _is_evidence(member, min_conf):
            continue
        stage = member.get('stage')
        transcript = stage in COARSE_MEMBER_STAGES
        precise = bool(transcript and member.get(f'precise_{edge}'))
        value = member[edge] if edge == 'end' else -member[edge]
        rank = (value, precise, finite_number(member.get('confidence')) or 0.0)
        candidates.append((rank, member[edge], 'transcript' if transcript else stage, precise))
    best = max(candidates, default=None)
    return {'envelope': finite_number(ad.get(edge)),
            'measured': None if best is None else best[1],
            'source': None if best is None else best[2],
            'precise': False if best is None else best[3]}


def reviewer_independent_spans(ad: dict, min_conf: float) -> list[tuple[float, float]]:
    """Measured spans a transcript-supported reviewer edge may not enter."""
    spans = [(m['start'], m['end']) for m in hard_members(ad, min_conf)
             if m.get('stage') in MEASURED_EVIDENCE_STAGES and not _unmatched_fingerprint(m)]
    pair = ad.get('cue_pair') or {}
    cue_lo = finite_number((pair.get('start') or {}).get('cue_end'))
    cue_hi = finite_number((pair.get('end') or {}).get('cue_start'))
    # Same 0.05 s pad cue_pair_ads puts inside each cue.
    if cue_lo is not None and cue_hi is not None and cue_hi - cue_lo > 0.1:
        spans.append((cue_lo + 0.05, cue_hi - 0.05))
    spans.extend(dai_probe_spans(ad))
    start, end = finite_number(ad.get('start')), finite_number(ad.get('end'))
    if ((ad.get('validation') or {}).get('user_confirmed')
            and start is not None and end is not None and end > start):
        spans.append((start, end))
    return spans


def union_cover(spans, start: float, end: float,
                gap_tol: float = COVERAGE_GAP_TOLERANCE,
                edge_tol: float = EDGE_TOLERANCE) -> tuple[float | None, float | None]:
    """Leftmost gap-tolerant run of spans in [start, end], edges snapped within edge_tol."""
    clipped = []
    for raw_lo, raw_hi in spans:
        lo, hi = finite_number(raw_lo), finite_number(raw_hi)
        if lo is None or hi is None:
            continue
        lo, hi = max(lo, start), min(hi, end)
        if hi > lo:
            clipped.append((lo, hi))
    if not clipped:
        return None, None
    lo, cursor = merge_runs(clipped, gap=gap_tol)[0]
    if lo <= start + edge_tol:
        lo = start
    if cursor >= end - edge_tol:
        cursor = end
    return lo, cursor


def merge_runs(spans, gap: float = 0.0, joins=None) -> list[list[float]]:
    """Sorted [lo, hi] runs of (lo, hi) spans; a span within gap of a run, or that joins accepts, extends it."""
    runs = []
    for lo, hi in sorted(spans):
        if runs and (lo <= runs[-1][1] + gap
                     or (joins is not None and joins(runs[-1][1], lo))):
            runs[-1][1] = max(runs[-1][1], hi)
        else:
            runs.append([lo, hi])
    return runs


def subtract_spans(pieces, spans):
    """Remove each (start, end) in spans from the (lo, hi) pieces."""
    for start, end in spans:
        pieces = [part for lo, hi in pieces
                  for part in ((lo, min(hi, start)), (max(lo, end), hi))
                  if part[1] > part[0]]
    return pieces


def mark_distinct_merge(target: dict, other: dict) -> None:
    """The one primitive every distinct-ad merge site calls: records the
    protected-member union and sets the merged_distinct_ads flag together,
    so a future merge site cannot set the flag while forgetting the
    bookkeeping. Call BEFORE mutating target's span or stage."""
    note_merged_members(target, other)
    target['merged_distinct_ads'] = True


def note_fold(target: dict, other: dict) -> None:
    """Bookkeeping every fold owes: touching or gapped spans are distinct ads
    and stay expand-only."""
    if other['start'] >= target['end']:
        mark_distinct_merge(target, other)
    else:
        note_merged_members(target, other)


def learning_bounds(ad: dict) -> tuple[float, float]:
    """The span pattern learning reads: measured bounds when silence was cut with the ad."""
    start, end = ad['start'], ad['end']
    measured = ad.get('_learning_bounds')
    if measured:
        lo, hi = max(measured[0], start), min(measured[1], end)
        if hi > lo:
            return lo, hi
    return start, end


def carve_fragment(parent: dict, start: float, end: float) -> dict:
    """Copy of parent narrowed to [start, end], minus the merge bookkeeping that
    described the wider span."""
    fragment = {k: v for k, v in parent.items()
                if k not in _MERGE_BOOKKEEPING_KEYS}
    fragment['start'] = start
    fragment['end'] = end
    clip_dai_core_spans(fragment, start, end)
    return fragment


def is_carved(marker: dict) -> bool:
    """Whether the marker is a fragment carved from a wider detected span."""
    return 'carved_from' in marker


def carve_partly_cut(marker: dict, start: float, end: float, covered) -> list[dict]:
    """Cut fragments over covered, uncut remainders elsewhere in [start, end]; [] if none covered."""
    covered = [(lo, hi) for lo, hi in covered if hi - lo > EDGE_TOLERANCE]
    if not covered:
        return []
    uncovered = [(lo, hi) for lo, hi in subtract_spans([(start, end)], covered)
                 if hi - lo > EDGE_TOLERANCE]
    origin = marker.get('carved_from') or {'start': marker['start'], 'end': marker['end']}
    fragments = []
    for lo, hi, was_cut in sorted([(lo, hi, True) for lo, hi in covered]
                                  + [(lo, hi, False) for lo, hi in uncovered]):
        fragment = carve_fragment(marker, lo, hi)
        fragment['carved_from'] = dict(origin)
        fragment['was_cut'] = was_cut
        fragments.append(fragment)
    return fragments


# Verdict fields the winning record owns on a fold, absences included.
# A winner never inherits the loser's hold id or pass-2 outcome.
_FOLD_VERDICT_FIELDS = ('action_applied', 'held_for_review', 'hold_reason', 'hold_id', 'pass2_outcome')


def _stayed_in_audio(marker: dict) -> bool:
    """A marker is uncut only when it says so. A missing or null was_cut predates
    the field and counts as cut, matching is_pending_review and count_not_cut."""
    was_cut = marker.get('was_cut', True)
    return was_cut is not None and not was_cut


def foldable_twin(markers, marker):
    """The uncut marker in markers naming the same span as marker; a cut marker
    never folds since its record must keep describing the removed audio."""
    if not isinstance(marker, dict) or not _stayed_in_audio(marker):
        return None
    return next(
        (m for m in markers
         if isinstance(m, dict) and m is not marker and _stayed_in_audio(m)
         and spans_match(m.get('start'), m.get('end'),
                         marker.get('start'), marker.get('end'))),
        None)


def _folded_validation(winner: dict, loser: dict) -> dict:
    """Validation for the folded marker: the winner's decision, the loser's
    fields where the winner has none, and the union of both flag lists."""
    won = winner.get('validation') or {}
    lost = loser.get('validation') or {}
    merged = {k: v for k, v in lost.items() if v is not None}
    merged.update({k: v for k, v in won.items() if v is not None})
    flags = list(lost.get('flags') or [])
    flags += [f for f in (won.get('flags') or []) if f not in flags]
    if flags:
        merged['flags'] = flags
    return merged


def _fold_rank(marker: dict) -> int:
    """Fold precedence: a keep is a settled decision, a hold an open question a
    human still owes an answer to, and a reject or plain record neither."""
    if marker.get('action_applied') == 'keep':
        return 2
    if marker.get('held_for_review'):
        return 1
    return 0


def fold_marker_pair(target: dict, other: dict) -> None:
    """Fold two markers stored for one span into target, in place. A keep verdict
    wins over a hold and a hold wins over a rejected or plain record; the loser
    fills any field the winner lacks, including a hold reason with nowhere else
    to go, which becomes a validation flag."""
    other_wins = _fold_rank(other) > _fold_rank(target)
    winner, loser = (other, target) if other_wins else (target, other)
    cleared = loser.get('hold_reason') if loser.get('held_for_review') else None
    merged = {k: v for k, v in loser.items() if v is not None}
    merged.update({k: v for k, v in winner.items() if v is not None})
    for field in _FOLD_VERDICT_FIELDS:
        if field in winner:
            merged[field] = winner[field]
        else:
            merged.pop(field, None)
    validation = _folded_validation(winner, loser)
    note = None
    if cleared and merged.get('held_for_review'):
        note = f'INFO: Pass 2 also held this span ({cleared})'
    elif cleared and merged.get('hold_cleared_reason', cleared) != cleared:
        note = f'INFO: A second hold was cleared on this span ({cleared})'
    elif cleared:
        merged['hold_cleared_reason'] = cleared
    if note:
        validation.setdefault('flags', []).append(note)
    if validation:
        merged['validation'] = validation
    target.clear()
    target.update(merged)


def collapse_duplicate_markers(markers):
    """Collapse markers stored twice for one span into one marker each.
    Returns ``(markers, folded_count)``; a clean list is left untouched."""
    collapsed = []
    folded = 0
    for marker in markers:
        twin = foldable_twin(collapsed, marker)
        if twin is None:
            collapsed.append(marker)
            continue
        fold_marker_pair(twin, marker)
        folded += 1
    return (collapsed, folded) if folded else (markers, 0)
