"""Marker-dict bookkeeping shared by the detector, validator, and reviewer."""
DAI_CORE_SPANS = 'dai_core_spans'


def _valid_dai_core_spans(marker: dict) -> list[dict[str, float]]:
    """Return normalized measured DAI spans from a marker.

    Invalid persisted values are ignored. Keeping this parser defensive lets
    old markers and hand-edited JSON pass through unchanged.
    """
    spans = []
    for raw in marker.get(DAI_CORE_SPANS) or []:
        if not isinstance(raw, dict):
            continue
        try:
            start = float(raw['start'])
            end = float(raw['end'])
        except (KeyError, TypeError, ValueError):
            continue
        if end > start:
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
