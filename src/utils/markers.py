"""Marker-dict bookkeeping shared by the detector, validator, and reviewer."""
from typing import Dict, Optional, Tuple

# Stages whose spans carry alignment-derived padding (especially tails)
# rather than transcript- or splice-anchored bounds. Members from these
# stages are trimmable by the reviewer; every other stage's span is
# protected inside a merge. Blacklist (not whitelist) so a future stage
# name fails conservative: unknown stages are protected.
UNPROTECTED_MEMBER_STAGES = frozenset({'dai_differential', 'vad_gap'})


def _protected_bounds(marker: Dict) -> Tuple[Optional[float], Optional[float]]:
    """Protected span one merge member contributes: its own recorded union
    when it was merged before, its span when its stage is anchored, else
    None/None."""
    if 'merged_protected_start' in marker:
        return marker['merged_protected_start'], marker['merged_protected_end']
    if marker.get('detection_stage') not in UNPROTECTED_MEMBER_STAGES:
        return marker['start'], marker['end']
    return None, None


def note_merged_members(target: Dict, other: Dict) -> None:
    """Record the protected-member union on a distinct-ad merge.

    Call BEFORE the merge mutates target's span or stage. Always writes
    merged_protected_start/end on target (None/None when no member is
    anchored) so the reviewer can tell a tracked merge from a legacy
    marker persisted by a pre-tracking release.
    """
    if 'merged_protected_start' not in target:
        target['merged_protected_start'], target['merged_protected_end'] = (
            _protected_bounds(target))
    o_lo, o_hi = _protected_bounds(other)
    if o_lo is None:
        return
    lo, hi = target['merged_protected_start'], target['merged_protected_end']
    target['merged_protected_start'] = o_lo if lo is None else min(lo, o_lo)
    target['merged_protected_end'] = o_hi if hi is None else max(hi, o_hi)


def mark_distinct_merge(target: Dict, other: Dict) -> None:
    """The one primitive every distinct-ad merge site calls: records the
    protected-member union and sets the merged_distinct_ads flag together,
    so a future merge site cannot set the flag while forgetting the
    bookkeeping. Call BEFORE mutating target's span or stage."""
    note_merged_members(target, other)
    target['merged_distinct_ads'] = True
