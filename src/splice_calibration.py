"""Per-feed splice-evidence calibration (spec 2.2).

Measures content base rates from the feed's recent stored splice_evidence
payloads and picks per-feed duration thresholds so the expected content
false-positive rate stays at or under SPLICE_CALIBRATION_MAX_FP_PER_HOUR.
Mirrors positional_prior's per-feed learning shape: computed from stored
history at pipeline time, additive, and it never raises into the pipeline.

Cold start (fewer than SPLICE_CALIBRATION_MIN_EPISODES usable episodes):
consumers may corroborate with splice events but must never veto.
"""
import json
import logging
from typing import Dict, Optional

from config import (
    SPLICE_CALIBRATION_MIN_EPISODES, SPLICE_CALIBRATION_RECENT_EPISODES,
    SPLICE_CALIBRATION_MAX_FP_PER_HOUR,
    SPLICE_DIGITAL_SILENCE_MIN_SECONDS, SPLICE_DEEP_SILENCE_MIN_SECONDS,
)

logger = logging.getLogger(__name__)

_SILENCE_TYPES = ('digital_silence', 'deep_silence')
_DEFAULT_MIN_S = {
    'digital_silence': SPLICE_DIGITAL_SILENCE_MIN_SECONDS,
    'deep_silence': SPLICE_DEEP_SILENCE_MIN_SECONDS,
}


def cold_start_calibration() -> Dict:
    """Conservative defaults used before a feed has enough history."""
    return {
        'status': 'cold_start',
        'episodes_considered': 0,
        'events_per_hour': {},
        'thresholds': {f'{t}_min_s': v for t, v in _DEFAULT_MIN_S.items()},
    }


def build_calibration(rows) -> Dict:
    """Build the calibration dict from stored history rows.

    rows: dicts with original_duration and audio_analysis_json (newest-first,
    from db.get_recent_audio_analyses).
    """
    total_hours = 0.0
    durations_by_type = {t: [] for t in _SILENCE_TYPES}
    considered = 0
    for row in rows:
        duration = row.get('original_duration') or 0
        if duration <= 0:
            continue
        try:
            analysis = json.loads(row['audio_analysis_json'])
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(analysis, dict):
            continue
        payload = analysis.get('splice_evidence')
        if not isinstance(payload, dict):
            continue
        considered += 1
        total_hours += duration / 3600.0
        for event in payload.get('events', []):
            etype = event.get('type')
            if etype in durations_by_type and event.get('duration_s') is not None:
                durations_by_type[etype].append(float(event['duration_s']))

    if considered < SPLICE_CALIBRATION_MIN_EPISODES or total_hours <= 0:
        return cold_start_calibration()

    rates = {}
    thresholds = {}
    allowed = int(total_hours * SPLICE_CALIBRATION_MAX_FP_PER_HOUR)
    for etype in _SILENCE_TYPES:
        durations = sorted(durations_by_type[etype], reverse=True)
        rates[etype] = round(len(durations) / total_hours, 3)
        default_min = _DEFAULT_MIN_S[etype]
        if len(durations) > allowed:
            # Raise the floor past the excess events; the longest survive.
            thresholds[f'{etype}_min_s'] = round(
                max(default_min, durations[allowed] + 0.1), 2)
        else:
            thresholds[f'{etype}_min_s'] = default_min

    return {
        'status': 'calibrated',
        'episodes_considered': considered,
        'events_per_hour': rates,
        'thresholds': thresholds,
    }


def compute_splice_calibration(db, slug: str,
                               exclude_episode_id: Optional[str] = None) -> Dict:
    """Load the feed's recent splice history and build its calibration.

    Never raises: calibration failure must not fail the pipeline.
    """
    try:
        rows = db.get_recent_audio_analyses(
            slug, exclude_episode_id=exclude_episode_id,
            limit=SPLICE_CALIBRATION_RECENT_EPISODES)
        return build_calibration(rows)
    except Exception as e:
        logger.warning(f"[{slug}] Splice calibration failed: {e}")
        return cold_start_calibration()
