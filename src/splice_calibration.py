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

from config import (
    SPLICE_CALIBRATION_MIN_EPISODES, SPLICE_CALIBRATION_RECENT_EPISODES,
    SPLICE_CALIBRATION_MAX_FP_PER_HOUR,
    SPLICE_CALIBRATED_MIN_CORROBORATED, SPLICE_HOST_READ_RECENT_EPISODES,
    SPLICE_DIGITAL_SILENCE_MIN_SECONDS, SPLICE_DEEP_SILENCE_MIN_SECONDS,
)

logger = logging.getLogger(__name__)

# Statuses whose splice events are trusted to extend a cut; host_read only stops the veto.
SPLICE_EVENTS_CALIBRATED_STATUSES = ('calibrated', 'host_read')

_SILENCE_TYPES = ('digital_silence', 'deep_silence')
_DEFAULT_MIN_S = {
    'digital_silence': SPLICE_DIGITAL_SILENCE_MIN_SECONDS,
    'deep_silence': SPLICE_DEEP_SILENCE_MIN_SECONDS,
}


def cold_start_calibration(episodes_considered: int = 0) -> dict:
    """Conservative defaults used before a feed has enough history.

    episodes_considered carries the count of valid payloads found so a
    below-gate cold_start payload stays diagnosable (e.g. 3 of 5). No-history
    and exception paths keep the default 0.
    """
    return {
        'status': 'cold_start',
        'episodes_considered': episodes_considered,
        'events_per_hour': {},
        'thresholds': {f'{t}_min_s': v for t, v in _DEFAULT_MIN_S.items()},
    }


def long_cut_corroboration(rows) -> dict:
    """Share of eligible long transcript-detected cuts with audio corroboration, from stored markers."""
    episodes = cuts = corroborated = 0
    for row in rows:
        try:
            markers = json.loads(row.get('ad_markers_json'))
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(markers, list):
            continue
        found = []
        origins = set()
        for m in markers:
            if not (isinstance(m, dict) and isinstance(m.get('validation'), dict)
                    and 'audio_corroboration' in m['validation']):
                continue
            # A reviewer reject is a false positive, not a standing cut.
            if m.get('source') == 'reviewer' and m.get('was_cut') is False:
                continue
            # Fragments carved from one detection, or pieces of one hold, count once.
            origin = m.get('carved_from')
            keys = {('hold', m['hold_id'])} if m.get('hold_id') else set()
            if isinstance(origin, dict):
                keys.add((origin.get('start'), origin.get('end')))
            if keys & origins:
                continue
            origins |= keys
            found.append(m['validation']['audio_corroboration'])
        if not found:
            continue
        episodes += 1
        cuts += len(found)
        corroborated += sum(source != 'none' for source in found)
    return {'episodes': episodes, 'cuts': cuts, 'corroborated': corroborated,
            'fraction': round(corroborated / cuts, 3) if cuts else None}


def build_calibration(rows, ad_history_rows=()) -> dict:
    """Build the calibration dict from stored history rows.

    rows: dicts with original_duration and audio_analysis_json (newest-first,
    from db.get_recent_audio_analyses). ad_history_rows: ad_markers_json rows
    (from db.get_recent_episode_ad_history) that decide calibrated vs host_read.

    The returned thresholds (digital_silence_min_s, deep_silence_min_s) are
    stored in the audio analysis payload as observability data and are visible
    via the API. They are NOT currently applied to event filtering: consumers
    gate on calibration.status only (spec 2.3c). Per-feed duration threshold
    suppression is deferred to a future reviewed change.
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
        return cold_start_calibration(considered)

    rates = {}
    thresholds = {}
    # Floor at 1 so feeds with < 1 hour of history still allow at least one
    # event per type rather than blocking all detection while status=calibrated.
    allowed = max(1, int(total_hours * SPLICE_CALIBRATION_MAX_FP_PER_HOUR))
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

    corroboration = long_cut_corroboration(ad_history_rows)
    # Until enough episodes carry the measurement the feed keeps today's status.
    host_read = (corroboration['episodes'] >= SPLICE_CALIBRATION_MIN_EPISODES
                 and corroboration['fraction'] < SPLICE_CALIBRATED_MIN_CORROBORATED)
    return {
        'status': 'host_read' if host_read else 'calibrated',
        'episodes_considered': considered,
        'events_per_hour': rates,
        'thresholds': thresholds,
        'long_cut_corroboration': corroboration,
    }


def compute_splice_calibration(db, slug: str,
                               exclude_episode_id: str | None = None) -> dict:
    """Load the feed's recent splice history and build its calibration.

    Never raises: calibration failure must not fail the pipeline.
    """
    try:
        rows = db.get_recent_audio_analyses(
            slug, exclude_episode_id=exclude_episode_id,
            limit=SPLICE_CALIBRATION_RECENT_EPISODES)
        try:
            ad_history = db.get_recent_episode_ad_history(
                slug, exclude_episode_id=exclude_episode_id,
                limit=SPLICE_HOST_READ_RECENT_EPISODES)
        except Exception as e:
            logger.warning(f"[{slug}] Long-cut corroboration history failed: {e}")
            ad_history = ()
        return build_calibration(rows, ad_history)
    except Exception as e:
        logger.warning(f"[{slug}] Splice calibration failed: {e}")
        return cold_start_calibration()
