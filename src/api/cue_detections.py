"""REST endpoints for cue detection telemetry (#350 follow-up).

Routes mounted under the ``/api/v1`` blueprint:

- ``POST /cue-detections/<id>/verdict``            - confirm / reject / reset a detection
- ``GET  /feeds/<slug>/cue-detections/advisory``   - per-feed cue health summary
- ``GET  /cue-detections/aggregate``               - global telemetry for threshold tuning

These are template-quality signals only. A verdict never changes an episode's
cut list; it records whether a template cue matched a real boundary so the user
can judge a feed's cues and tune thresholds.
"""
import logging

from flask import request

from api import (
    api, log_request, json_response, error_response, get_database,
)
from audio_analysis.cue_verdict_hints import template_verdict_hint
from config import resolve_cue_template_score

logger = logging.getLogger('podcast.api.cue_detections')

_VALID_VERDICTS = ('pending', 'confirmed', 'rejected')


@api.route('/cue-detections/<int:detection_id>/verdict', methods=['POST'])
@log_request
def set_cue_detection_verdict(detection_id):
    """Record the user's review verdict for one cue detection."""
    db = get_database()
    payload = request.get_json(silent=True) or {}
    verdict = payload.get('verdict')
    if verdict not in _VALID_VERDICTS:
        return error_response(
            'verdict must be one of: ' + ', '.join(_VALID_VERDICTS), 400)
    if not db.set_cue_detection_verdict(detection_id, verdict):
        return error_response('cue detection not found', 404)
    return json_response({'id': detection_id, 'verdict': verdict})


@api.route('/feeds/<slug>/cue-detections/advisory', methods=['GET'])
@log_request
def get_cue_detections_advisory(slug):
    """Per-feed cue health: outcome/verdict counts, score range, confirm rate."""
    db = get_database()
    podcast = db.get_podcast_by_slug(slug)
    if not podcast:
        return error_response('feed not found', 404)
    payload = db.cue_feed_advisory(podcast['id'])
    if not (payload['confirmed'] or payload['rejected']):
        payload['templateHints'] = []
        return json_response(payload)
    threshold = resolve_cue_template_score(db, podcast['id'])
    hints = []
    for t in db.cue_template_verdict_scores(podcast['id']):
        hint = template_verdict_hint(t['rejected'], t['confirmed'], threshold)
        if hint:
            hints.append({
                'templateId': t['templateId'], 'label': t['label'], 'hint': hint,
                'rejected': len(t['rejected']), 'confirmed': len(t['confirmed'])})
    payload['templateHints'] = hints
    return json_response(payload)


@api.route('/cue-detections/aggregate', methods=['GET'])
@log_request
def get_cue_detections_aggregate():
    """Global cue telemetry (score histogram + outcome/verdict totals) for tuning."""
    db = get_database()
    return json_response(db.cue_aggregate_stats())
