"""Marker-dict bookkeeping shared by the detector, validator, and reviewer."""
import math


DAI_CORE_SPANS = 'dai_core_spans'

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
                and abs(quote_time - edge_time) <= 0.05)


def invalidate_quote_alignment(marker: dict) -> None:
    for edge in ('start', 'end'):
        if marker.get(f'quote_aligned_{edge}') and not quote_edge_valid(marker, edge):
            marker.pop(f'quote_aligned_{edge}', None)
            marker.pop(f'quote_{edge}', None)
            marker.pop(f'quote_original_{edge}', None)


def word_timed_edge_valid(marker: dict, edge: str) -> bool:
    timed = finite_number(marker.get(f'word_timed_{edge}'))
    current = finite_number(marker.get(edge))
    return timed is not None and current is not None and abs(timed - current) <= 0.05


def invalidate_word_timed_edges(marker: dict) -> None:
    for edge in ('start', 'end'):
        if (f'word_timed_{edge}' in marker
                and not word_timed_edge_valid(marker, edge)):
            marker.pop(f'word_timed_{edge}', None)


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


def _clip_spans(marker: dict, key: str, start: float, end: float,
                extra_field: str | None = None,
                keep_empty: bool = False,
                optional_fields: tuple = ()) -> None:
    """Clip the spans stored under `key` into [start, end], dropping collapsed
    ones; `keep_empty` writes an empty list instead of removing the key."""
    if keep_empty and key not in marker:
        return
    clipped = []
    for span in _valid_spans(marker, key, extra_field, optional_fields):
        lo = max(start, span['start'])
        hi = min(end, span['end'])
        if hi > lo:
            # A moved edge is no longer the one that was measured.
            for edge, new in (('start', lo), ('end', hi)):
                if new != span[edge] and f'precise_{edge}' in span:
                    span[f'precise_{edge}'] = False
            span['start'], span['end'] = lo, hi
            clipped.append(span)
    if clipped or keep_empty:
        marker[key] = clipped
    else:
        marker.pop(key, None)


def _valid_dai_core_spans(marker: dict) -> list[dict[str, float]]:
    """Normalized measured DAI spans from a marker."""
    return _valid_spans(marker, DAI_CORE_SPANS)


def merge_dai_core_spans(target: dict, other: dict) -> None:
    """Carry measured DAI regions through a marker merge."""
    spans = _valid_dai_core_spans(target) + _valid_dai_core_spans(other)
    if not spans:
        return
    spans.sort(key=lambda span: span['start'])
    merged = [spans[0]]
    for span in spans[1:]:
        if span['start'] <= merged[-1]['end'] + 0.05:
            merged[-1]['end'] = max(merged[-1]['end'], span['end'])
        else:
            merged.append(span)
    target[DAI_CORE_SPANS] = merged


def clip_dai_core_spans(marker: dict, start: float, end: float) -> None:
    """Clip a marker's DAI evidence to a newly split/clamped range."""
    _clip_spans(marker, DAI_CORE_SPANS, start, end)


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
                  'fingerprint_match_start', 'fingerprint_match_end')

# Stages whose member span is measured regardless of confidence.
_MEASURED_MEMBER_STAGES = frozenset({'cue_pair', 'manual', 'text_pattern'})

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
    # keep_empty: the key's presence is what marks a tracked merge.
    _clip_spans(marker, MERGED_MEMBER_SPANS, start, end,
                extra_field='stage', keep_empty=True,
                optional_fields=_MEMBER_FIELDS)


def clip_merge_spans(marker: dict, lo: float, hi: float) -> None:
    """Clamp every merge record a marker carries into [lo, hi]."""
    for key in ('merged_protected_start', 'merged_protected_end'):
        bound = marker.get(key)
        if bound is not None:
            marker[key] = min(max(bound, lo), hi)
    clip_member_spans(marker, lo, hi)


def _member_spans(marker: dict) -> list[dict]:
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
    text = estimated_text_bounds(marker)
    if text is not None:
        # An estimate protects only its matched text; a span holding none of
        # it carries no evidence.
        if text[1] <= text[0]:
            return []
        lo, hi = text
    stage = marker.get('detection_stage')
    member = {'start': lo, 'end': hi, 'stage': stage}
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


def _coalesce_coarse_members(spans: list[dict]) -> list[dict]:
    """Union overlapping same-stage coarse members: two LLM windows over one ad
    are one member, not two the reviewer has to keep separately."""
    merged: list[dict] = []
    for span in spans:
        stage = span.get('stage')
        prior = next(
            (m for m in merged if m.get('stage') == stage
             and stage in COARSE_MEMBER_STAGES
             and span['start'] <= m['end'] and span['end'] >= m['start']),
            None) if stage in COARSE_MEMBER_STAGES else None
        if prior is None:
            merged.append(span)
            continue
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
    spans = _coalesce_coarse_members(_member_spans(target) + _member_spans(other))
    target[MERGED_MEMBER_SPANS] = spans
    # A union already on the target only widens: a legacy bound has no member
    # span backing it.
    lo, hi = span_bounds(spans)
    target['merged_protected_start'] = _widen(
        target.get('merged_protected_start'), lo, min)
    target['merged_protected_end'] = _widen(
        target.get('merged_protected_end'), hi, max)


def measured_member_spans(marker: dict, min_conf: float) -> list[tuple[float, float]]:
    """Spans of audio a marker's members measured, sorted by start."""
    spans = [(s['start'], s['end']) for s in _valid_dai_core_spans(marker)]
    for member in _member_spans(marker):
        stage = member.get('stage')
        lo, hi = member['start'], member['end']
        if stage == 'fingerprint':
            match_lo = finite_number(member.get('fingerprint_match_start'))
            match_hi = finite_number(member.get('fingerprint_match_end'))
            if match_lo is None or match_hi is None:
                continue
            lo, hi = max(lo, match_lo), min(hi, match_hi)
        elif stage in COARSE_MEMBER_STAGES and stage != 'keep_content':
            confidence = finite_number(member.get('confidence'))
            if confidence is None or confidence < min_conf:
                continue
        elif stage not in _MEASURED_MEMBER_STAGES:
            continue
        if hi > lo:
            spans.append((lo, hi))
    return sorted(spans)


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
    clipped.sort()
    lo, cursor = clipped[0]
    for span_lo, span_hi in clipped[1:]:
        if span_lo > cursor + gap_tol:
            break
        cursor = max(cursor, span_hi)
    if lo <= start + edge_tol:
        lo = start
    if cursor >= end - edge_tol:
        cursor = end
    return lo, cursor


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


def carve_fragment(parent: dict, start: float, end: float) -> dict:
    """Copy of parent narrowed to [start, end], minus the merge bookkeeping that
    described the wider span."""
    fragment = {k: v for k, v in parent.items()
                if k not in _MERGE_BOOKKEEPING_KEYS}
    fragment['start'] = start
    fragment['end'] = end
    clip_dai_core_spans(fragment, start, end)
    return fragment


# Verdict fields the winning record owns on a fold, absences included.
_FOLD_VERDICT_FIELDS = ('action_applied', 'held_for_review', 'hold_reason')


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
