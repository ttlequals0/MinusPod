"""Build per-cue detection telemetry from a finished first-pass (#350 follow-up).

Pure function over the final ad list and the audio analysis result. For every
template cue the matcher surfaced it records the match score and how detection
used the cue: ``pair`` (it bracketed a synthesized ad), ``snap`` (it moved an ad
edge), or ``none`` (it was surfaced but did not affect the cut). Spectral cues
are not recorded -- they carry no template identity to review or promote.
"""
from typing import Dict, List, Optional

from config import AUDIO_CUE_SOURCE_TEMPLATE, is_template_cue


def _cue_key(template_id, start):
    """Stable (template_id, start) key for matching a cue to a pair/snap record.

    Rounds to 3 decimals -- the same precision cue_pair/cue_snap store cue_start
    at -- so the match is exact and two near-adjacent cues of the same template
    do not collapse into one key.
    """
    try:
        return (template_id, round(float(start), 3))
    except (TypeError, ValueError):
        return (template_id, None)


def build_cue_detection_records(ads: List[Dict],
                                audio_analysis_result) -> List[Dict]:
    """Return one telemetry record per template cue in ``audio_analysis_result``."""
    if not audio_analysis_result:
        return []
    try:
        cues = audio_analysis_result.get_signals_by_type('audio_cue')
    except AttributeError:
        return []

    paired = set()
    snapped = set()
    for ad in ads or []:
        for side in ('start', 'end'):
            sub = (ad.get('cue_pair') or {}).get(side)
            if sub:
                paired.add(_cue_key(sub.get('template_id'), sub.get('cue_start')))
            snap = (ad.get('cue_snap') or {}).get(side)
            if snap:
                snapped.add(_cue_key(snap.get('template_id'), snap.get('cue_start')))

    records = []
    for c in cues:
        details = c.details or {}
        if not is_template_cue(details):
            continue
        key = _cue_key(details.get('template_id'), c.start)
        if key in paired:
            outcome = 'pair'
        elif key in snapped:
            outcome = 'snap'
        else:
            outcome = 'none'
        records.append({
            'template_id': details.get('template_id'),
            'label': details.get('label'),
            'cue_type': details.get('cue_type'),
            'role': details.get('role'),
            'source': AUDIO_CUE_SOURCE_TEMPLATE,
            'start_s': round(float(c.start), 3),
            'end_s': round(float(c.end), 3),
            'match_score': _as_float(details.get('score')),
            'confidence': round(float(c.confidence), 3),
            'outcome': outcome,
        })
    return records


def _as_float(value) -> Optional[float]:
    try:
        return round(float(value), 3)
    except (TypeError, ValueError):
        return None
