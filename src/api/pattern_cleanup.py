"""Pattern cleanup routes; see pattern_cleanup.py for the service. CSRF for POST/PUT is enforced globally (api/__init__.py), not per-route here."""
import logging

from flask import request

import pattern_cleanup
from api import api, limiter, log_request, json_response, error_response, get_database
from database.settings import registry_default, registry_get_default

logger = logging.getLogger('podcast.api')

KNOWN_KINDS = ('trim', 'split', 'rename', 'retire', 'flag', 'category')
KNOWN_STATUSES = ('pending', 'approved', 'rejected', 'undone')


def cleanup_settings_view(db) -> dict:
    """Pattern cleanup settings in the API's camelCase form."""
    model = db.get_setting('pattern_cleanup_model')
    return {
        'enabled': db.get_setting_bool(
            'pattern_cleanup_enabled', registry_get_default('pattern_cleanup_enabled')),
        'cron': db.get_setting('pattern_cleanup_cron') or registry_default('pattern_cleanup_cron'),
        'batchSize': db.get_setting_int(
            'pattern_cleanup_batch_size', registry_get_default('pattern_cleanup_batch_size')),
        'unusedDays': db.get_setting_int(
            'pattern_cleanup_unused_days', registry_get_default('pattern_cleanup_unused_days')),
        'provider': db.get_setting('pattern_cleanup_provider') or '',
        'model': model or '',
        'modelMissing': model == '',
    }


def _camel_key(key: str) -> str:
    head, *rest = key.split('_')
    return head + ''.join(word.capitalize() for word in rest)


def _camelize(value):
    """Recursively camelCase dict keys; the API is camelCase everywhere, DB storage is not."""
    if isinstance(value, dict):
        return {_camel_key(k): bool(v) if k == 'is_active' and v is not None else _camelize(v)
                for k, v in value.items()}
    if isinstance(value, list):
        return [_camelize(v) for v in value]
    return value


def _pattern_summary_view(pattern: dict) -> dict:
    return {
        'id': pattern.get('id'),
        'sponsor': pattern.get('sponsor'),
        'scope': pattern.get('scope'),
        'networkId': pattern.get('network_id'),
        'podcastTitle': pattern.get('podcast_title'),
        'isActive': bool(pattern.get('is_active')),
        'confirmationCount': pattern.get('confirmation_count') or 0,
        'falsePositiveCount': pattern.get('false_positive_count') or 0,
        'lastMatchedAt': pattern.get('last_matched_at'),
        'createdAt': pattern.get('created_at'),
    }


def _suggestion_view(s: dict) -> dict:
    view = {
        'id': s['id'],
        'runId': s.get('run_id'),
        'patternId': s['pattern_id'],
        'kind': s['kind'],
        'status': s['status'],
        'confidence': s.get('confidence'),
        'reasons': s.get('reasons') or [],
        'payload': _camelize(s.get('payload') or {}),
        'before': _camelize(s.get('before')),
        'applied': _camelize(s.get('applied')),
        'createdAt': s.get('created_at'),
        'reviewedAt': s.get('reviewed_at'),
    }
    if 'pattern' in s:
        view['pattern'] = _pattern_summary_view(s['pattern'])
    return view


def _run_view(r: dict) -> dict:
    return {
        'id': r['id'],
        'startedAt': r.get('started_at'),
        'finishedAt': r.get('finished_at'),
        'status': r.get('status'),
        'forced': bool(r.get('forced')),
        'trigger': r.get('trigger'),
        'model': r.get('model'),
        'provider': r.get('provider'),
        'credentialSlot': r.get('credential_slot'),
        'reviewedCount': r.get('reviewed_count') or 0,
        'suggestedCount': r.get('suggested_count') or 0,
        'skippedCount': r.get('skipped_count') or 0,
        'errorCount': r.get('error_count') or 0,
        'error': r.get('error'),
    }


@api.route('/patterns/cleanup', methods=['GET'])
@log_request
def get_pattern_cleanup_status():
    db = get_database()
    latest = db.get_cleanup_runs(limit=1)
    finished = db.get_latest_finished_cleanup_run()
    view = cleanup_settings_view(db)
    view.update({
        'inProgress': pattern_cleanup.is_cleanup_running(db),
        'lastRun': latest[0]['started_at'] if latest else None,
        'lastError': (finished or {}).get('error') or None,
        'lastSummary': _run_view(finished) if finished else None,
        'pending': db.get_cleanup_pending_counts(),
    })
    return json_response(view)


@api.route('/patterns/cleanup/run', methods=['POST'])
# A validation 400 does not consume the budget; only a started run or an in-progress conflict does.
@limiter.limit('6/hour', deduct_when=lambda response: response.status_code != 400)
@log_request
def run_pattern_cleanup():
    data = request.get_json(silent=True) if request.get_data() else {}
    if not isinstance(data, dict):
        return error_response('request body must be a JSON object', 400)
    force = data.get('force', False)
    if not isinstance(force, bool):
        return error_response('force must be a boolean', 400)
    db = get_database()
    try:
        run_id = pattern_cleanup.start_cleanup_run(db, force=force, trigger='manual')
    except pattern_cleanup.UnsupportedCleanupRouteError as error:
        return error_response(str(error), 400)
    if run_id is None:
        return error_response('cleanup_in_progress', 409)
    return json_response({'runId': run_id}, 202)


@api.route('/patterns/cleanup/runs', methods=['GET'])
@log_request
def list_cleanup_runs():
    limit = max(1, min(100, request.args.get('limit', default=20, type=int) or 20))
    db = get_database()
    return json_response({'runs': [_run_view(r) for r in db.get_cleanup_runs(limit=limit)]})


@api.route('/patterns/cleanup/suggestions', methods=['GET'])
@log_request
def list_cleanup_suggestions():
    status = request.args.get('status')
    kind = request.args.get('kind')
    if status is not None and status not in KNOWN_STATUSES:
        return error_response(f'status must be one of: {", ".join(KNOWN_STATUSES)}', 400)
    if kind is not None and kind not in KNOWN_KINDS:
        return error_response(f'kind must be one of: {", ".join(KNOWN_KINDS)}', 400)
    limit = max(1, min(200, request.args.get('limit', default=50, type=int) or 50))
    offset = max(0, request.args.get('offset', default=0, type=int) or 0)
    before_id = request.args.get('before_id', default=None, type=int)
    db = get_database()
    items = db.get_cleanup_suggestions(
        status=status, kind=kind, limit=limit, offset=offset, before_id=before_id)
    return json_response({'suggestions': [_suggestion_view(i) for i in items]})


def _resolve_suggestion_action(fn, suggestion_id):
    db = get_database()
    try:
        suggestion = fn(db, suggestion_id)
    except pattern_cleanup.SuggestionNotFoundError:
        return error_response('suggestion not found', 404)
    except pattern_cleanup.SuggestionStateError:
        return error_response('invalid_transition', 409)
    return json_response(_suggestion_view(suggestion))


@api.route('/patterns/cleanup/suggestions/<int:suggestion_id>/approve', methods=['POST'])
@log_request
def approve_cleanup_suggestion(suggestion_id):
    return _resolve_suggestion_action(pattern_cleanup.apply_suggestion, suggestion_id)


@api.route('/patterns/cleanup/suggestions/<int:suggestion_id>/reject', methods=['POST'])
@log_request
def reject_cleanup_suggestion(suggestion_id):
    return _resolve_suggestion_action(pattern_cleanup.reject_suggestion, suggestion_id)


@api.route('/patterns/cleanup/suggestions/<int:suggestion_id>/undo', methods=['POST'])
@log_request
def undo_cleanup_suggestion(suggestion_id):
    return _resolve_suggestion_action(pattern_cleanup.undo_suggestion, suggestion_id)


@api.route('/patterns/cleanup/suggestions/bulk', methods=['POST'])
@log_request
def bulk_cleanup_suggestions():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return error_response('request body must be a JSON object', 400)
    ids = data.get('ids')
    action = data.get('action')
    if (not isinstance(ids, list) or not ids
            or any(not isinstance(i, int) or isinstance(i, bool) for i in ids)):
        return error_response('ids must be a non-empty array of integers', 400)
    if action not in ('approve', 'reject'):
        return error_response('action must be "approve" or "reject"', 400)
    db = get_database()
    fn = pattern_cleanup.apply_suggestion if action == 'approve' else pattern_cleanup.reject_suggestion
    results = []
    for sid in ids:
        try:
            suggestion = fn(db, sid)
            results.append({'id': sid, 'status': suggestion['status']})
        except pattern_cleanup.SuggestionNotFoundError:
            results.append({'id': sid, 'error': 'not_found'})
        except pattern_cleanup.SuggestionStateError:
            results.append({'id': sid, 'error': 'invalid_transition'})
    return json_response({'results': results})
