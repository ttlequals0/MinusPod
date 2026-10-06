"""Failover state, manual trigger and cancel, and on-demand probes (#806)."""
import logging

from flask import request
from werkzeug.exceptions import BadRequest, UnsupportedMediaType

import failover
from api import api, error_response, json_response, log_request, get_database

logger = logging.getLogger(__name__)
_TARGET_API_NAME = {v: k for k, v in failover.API_TARGET_NAMES.items()}


def _target_view(target: str) -> dict:
    return {**failover.state(target), 'configured': failover.is_configured(target),
            'enabled': failover.target_enabled(target)}


def _probe_view(data: dict) -> dict:
    return {'reachable': data['reachable'], 'status': data['status'], 'detail': data['detail'],
            'checkedAt': data['checked_at'], 'healthyStreak': data['healthy_streak'],
            'failedStreak': data['failed_streak']}


def probes_view() -> dict:
    return {failover.PROBE_API_NAMES[t]: _probe_view(s) for t, s in failover.all_probe_states().items()}


def targets_view() -> dict:
    return {name: _target_view(t) for name, t in failover.API_TARGET_NAMES.items()}


def _event_view(e: dict) -> dict:
    return {'id': e['id'], 'target': _TARGET_API_NAME.get(e['target'], e['target']),
            'action': e['action'], 'source': e['source'], 'reason': e['reason'],
            'createdAt': e['created_at']}


@api.route('/failover', methods=['GET'])
@log_request
def get_failover():
    return json_response({
        'targets': targets_view(),
        'probes': probes_view(),
        'policy': {'probeIntervalMinutes': failover.probe_interval_seconds() // 60,
                   'recoveryProbes': failover.recovery_probes()},
        'events': [_event_view(e) for e in failover.recent_events(50)],
    })


@api.route('/failover/<name>/trigger', methods=['POST'])
@log_request
def trigger_failover(name):
    target = failover.API_TARGET_NAMES.get(name)
    if target is None:
        return error_response('unknown failover target', 404)
    if not failover.target_enabled(target):
        return error_response('failover_target_disabled', 409)
    if not failover.is_configured(target):
        return error_response('failover_not_configured', 409)
    try:
        body = request.get_json() if request.get_data() else None
    except (BadRequest, UnsupportedMediaType):
        return error_response('request body must be valid JSON', 400)
    if body is None:
        body = {}
    if not isinstance(body, dict):
        return error_response('request body must be a JSON object', 400)
    reason = body.get('reason', '')
    if not isinstance(reason, str):
        return error_response('reason must be a string', 400)
    try:
        failover.trigger(target, reason or 'manual trigger', source='manual', raise_on_error=True)
    except failover.FailoverTransitionError:
        return error_response('failover_transition_failed', 503)
    return json_response({'target': name, 'state': _target_view(target)})


@api.route('/failover/<name>/cancel', methods=['POST'])
@log_request
def cancel_failover(name):
    target = failover.API_TARGET_NAMES.get(name)
    if target is None:
        return error_response('unknown failover target', 404)
    try:
        failover.cancel(target, source='manual', raise_on_error=True)
    except failover.FailoverTransitionError:
        return error_response('failover_transition_failed', 503)
    return json_response({'target': name, 'state': _target_view(target)})


@api.route('/failover/probe', methods=['POST'])
@log_request
def probe_failover():
    db = get_database()
    before = failover.all_probe_states()
    failover.probe_tick(db)
    after = failover.all_probe_states()
    recovered = any(
        after[target]['reachable'] is True and before[target]['reachable'] is not True
        for target in after
    )
    if recovered and db.get_deferred_episodes():
        # Deferred import here to avoid circular import at app start (main_app imports api before main_app.background).
        from main_app.background import _offline_queue_target_resolver
        from offline_queue import offline_queue_tick
        offline_queue_tick(db, _offline_queue_target_resolver)
    return json_response({'probes': probes_view()})
