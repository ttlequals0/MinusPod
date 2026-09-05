"""Marker-dict bookkeeping shared by the detector, validator, and reviewer."""
import math


DAI_CORE_SPANS = 'dai_core_spans'


def invalidate_tail_provenance(marker: dict, new_end: float) -> None:
    """Drop tail-growth eligibility when a stage selects a new end.

    Content-tail provenance describes how the current edge was reached. A
    reviewer or human-selected end must earn any later sonic-tail extension
    from fresh evidence instead of reusing stale eligibility.
    """
    if marker.get('end') != new_end:
        marker.pop('end_extended_by_content', None)
        marker.pop('tail_splice_snap', None)


def _valid_dai_core_spans(marker: dict) -> list[dict[str, float]]:
    """Return normalized measured DAI spans from a marker.

    Invalid persisted values are ignored. Keeping this parser defensive lets
    old markers and hand-edited JSON pass through unchanged.
    """
    raw_spans = marker.get(DAI_CORE_SPANS)
    if not isinstance(raw_spans, list):
        return []
    spans = []
    for raw in raw_spans:
        if not isinstance(raw, dict):
            continue
        try:
            raw_start = raw['start']
            raw_end = raw['end']
        except (KeyError, OverflowError, TypeError, ValueError):
            continue
        # bool is a subclass of int, and float(True) silently becomes 1.0.
        # Optional evidence with boolean endpoints is malformed, not a
        # measured one-second span at the beginning of the episode.
        if isinstance(raw_start, bool) or isinstance(raw_end, bool):
            continue
        try:
            start = float(raw_start)
            end = float(raw_end)
        except (OverflowError, TypeError, ValueError):
            continue
        if math.isfinite(start) and math.isfinite(end) and end > start:
            spans.append({'start': start, 'end': end})
    return spans


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
    clipped = []
    for span in _valid_dai_core_spans(marker):
        lo = max(start, span['start'])
        hi = min(end, span['end'])
        if hi > lo:
            clipped.append({'start': lo, 'end': hi})
    if clipped:
        marker[DAI_CORE_SPANS] = clipped
    else:
        marker.pop(DAI_CORE_SPANS, None)


def dai_core_bounds(marker: dict) -> tuple[float | None, float | None]:
    """Return the outer bounds of measured DAI evidence, if present."""
    spans = _valid_dai_core_spans(marker)
    if not spans:
        return None, None
    return min(s['start'] for s in spans), max(s['end'] for s in spans)

# Stages whose spans carry alignment-derived padding (especially tails)
# rather than transcript- or splice-anchored bounds. Members from these
# stages are trimmable by the reviewer; every other stage's span is
# protected inside a merge. Blacklist (not whitelist) so a future stage
# name fails conservative: unknown stages are protected.
UNPROTECTED_MEMBER_STAGES = frozenset({'dai_differential', 'vad_gap'})


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


def note_merged_members(target: dict, other: dict) -> None:
    """Record the protected-member union on a distinct-ad merge.

    Call BEFORE the merge mutates target's span or stage. Always writes
    merged_protected_start/end on target (None/None when no member is
    anchored) so the reviewer can tell a tracked merge from a legacy
    marker persisted by a pre-tracking release.
    """
    merge_dai_core_spans(target, other)
    if 'merged_protected_start' not in target:
        target['merged_protected_start'], target['merged_protected_end'] = (
            _protected_bounds(target))
    o_lo, o_hi = _protected_bounds(other)
    if o_lo is not None:
        lo = target['merged_protected_start']
        target['merged_protected_start'] = o_lo if lo is None else min(lo, o_lo)
    if o_hi is not None:
        hi = target['merged_protected_end']
        target['merged_protected_end'] = o_hi if hi is None else max(hi, o_hi)


def mark_distinct_merge(target: dict, other: dict) -> None:
    """The one primitive every distinct-ad merge site calls: records the
    protected-member union and sets the merged_distinct_ads flag together,
    so a future merge site cannot set the flag while forgetting the
    bookkeeping. Call BEFORE mutating target's span or stage."""
    note_merged_members(target, other)
    target['merged_distinct_ads'] = True


# Both-edges tolerance for treating two markers as the same span. Matches
# _find_marker_in_list in api/patterns.py, the reject path, and the review
# listing, so every consumer agrees on what one span means.
BOUNDS_TOLERANCE_S = 0.5

# Verdict fields the winning record owns on a fold, absences included.
_FOLD_VERDICT_FIELDS = ('action_applied', 'held_for_review', 'hold_reason')


def spans_match(a_start, a_end, b_start, b_end,
                tol: float = BOUNDS_TOLERANCE_S) -> bool:
    """Both edges within tol seconds."""
    if a_start is None or a_end is None or b_start is None or b_end is None:
        return False
    return abs(a_start - b_start) <= tol and abs(a_end - b_end) <= tol


def foldable_twin(markers, marker):
    """The uncut marker in markers naming the same span as marker; a cut marker
    never folds since its record must keep describing the removed audio."""
    if not isinstance(marker, dict) or marker.get('was_cut'):
        return None
    return next(
        (m for m in markers
         if isinstance(m, dict) and m is not marker and not m.get('was_cut')
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


def fold_marker_pair(target: dict, other: dict) -> None:
    """Fold two markers stored for one span into target, in place. A keep verdict
    wins over a hold; the loser fills any field the winner lacks, including a
    hold reason with nowhere else to go, which becomes a validation flag."""
    other_wins = (other.get('action_applied') == 'keep'
                  and target.get('action_applied') != 'keep')
    winner, loser = (other, target) if other_wins else (target, other)
    cleared = loser.get('hold_reason') if loser.get('held_for_review') else None
    merged = {k: v for k, v in loser.items() if v is not None}
    merged.update({k: v for k, v in winner.items() if v is not None})
    # Callers fold uncut pairs only; foldable_twin admits no other pair.
    merged['was_cut'] = False
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

    Returns ``(markers, folded_count)``; the list is rebuilt only when
    something folded, so a clean row is left untouched.
    """
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
