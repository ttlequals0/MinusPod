"""Merge persisted audio-cue signals with loud-spot bursts into one candidate
list for the episode-page "make a template" surface (#350 follow-up).

Each candidate is a plain dict the API serializes directly. A loud-spot that
sits within a small tolerance of an already-detected cue is dropped, since the
persisted cue is the richer record (it carries a label / score / type).
"""
from typing import Dict, List, Optional

DEDUP_TOLERANCE_S = 0.75
DEFAULT_LIMIT = 100


def _as_float(value) -> Optional[float]:
    try:
        return round(float(value), 3)
    except (TypeError, ValueError):
        return None


def build_detected_cues(cue_signals: List[Dict], loud_spots: List[Dict],
                        dedup_tolerance_s: float = DEDUP_TOLERANCE_S,
                        limit: int = DEFAULT_LIMIT) -> List[Dict]:
    """Return merged, source-labeled cue candidates sorted by start time.

    cue_signals: ``audio_cue`` entries from a persisted
        ``AudioAnalysisResult.to_dict()`` (each ``{'start','end','details': {...}}``).
    loud_spots: ``{'start','end','prominenceDb'}`` bursts from the loud-spot pass.
    """
    items = []
    cue_starts = []
    for s in cue_signals:
        start = _as_float(s.get('start'))
        end = _as_float(s.get('end'))
        if start is None or end is None:
            continue
        details = s.get('details') or {}
        items.append({
            'start': start,
            'end': end,
            'source': details.get('source') or 'spectral',
            'label': details.get('label'),
            'cueType': details.get('cue_type'),
            'score': _as_float(details.get('score')),
            'prominenceDb': _as_float(details.get('prominence_db')),
        })
        cue_starts.append(start)

    for ls in loud_spots:
        start = _as_float(ls.get('start'))
        end = _as_float(ls.get('end'))
        if start is None or end is None:
            continue
        if any(abs(start - cs) <= dedup_tolerance_s for cs in cue_starts):
            continue
        items.append({
            'start': start,
            'end': end,
            'source': 'loud_spot',
            'label': None,
            'cueType': None,
            'score': None,
            'prominenceDb': _as_float(ls.get('prominenceDb')),
        })

    items.sort(key=lambda x: x['start'])
    return items[:limit]
