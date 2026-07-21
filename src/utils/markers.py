"""Marker-dict bookkeeping shared by the detector, validator, and reviewer."""
from typing import Dict

# Stages whose spans are transcript- or pattern-anchored and therefore
# precise. Differential regions and VAD gaps carry alignment-derived
# padding, especially at the tail, and are NOT protected: the reviewer is
# allowed to trim into them.
PROTECTED_MEMBER_STAGES = frozenset({
    'fingerprint', 'text_pattern', 'claude', 'verification',
})


def note_merged_members(target: Dict, other: Dict) -> None:
    """Record the protected-member union on a distinct-ad merge.

    Call BEFORE the merge mutates target's span or stage. Always writes
    merged_protected_start/end on target (None/None when no member is
    anchored) so the reviewer can tell a tracked merge from a legacy
    marker persisted by a pre-tracking release.
    """
    if 'merged_protected_start' not in target:
        if target.get('detection_stage') in PROTECTED_MEMBER_STAGES:
            target['merged_protected_start'] = target['start']
            target['merged_protected_end'] = target['end']
        else:
            target['merged_protected_start'] = None
            target['merged_protected_end'] = None
    if 'merged_protected_start' in other:
        o_lo, o_hi = other['merged_protected_start'], other['merged_protected_end']
    elif other.get('detection_stage') in PROTECTED_MEMBER_STAGES:
        o_lo, o_hi = other['start'], other['end']
    else:
        o_lo = o_hi = None
    if o_lo is None:
        return
    lo, hi = target['merged_protected_start'], target['merged_protected_end']
    target['merged_protected_start'] = o_lo if lo is None else min(lo, o_lo)
    target['merged_protected_end'] = o_hi if hi is None else max(hi, o_hi)
