"""Show-bound community segment routes; responses carry category, state and counts, never hashes or bucket."""
import logging

from flask import request

import community_segments as seg
from api import api, error_response, get_database, json_response, log_request

logger = logging.getLogger('podcast.api')

_ACTIONS = ('suspend', 'reactivate', 'protect', 'unprotect')


def _public(entry: dict, counts: dict) -> dict:
    return {'id': entry['id'], 'category': entry['category'], 'shortAsk': bool(seg.body(entry).get('short_ask')),
            'state': entry['state'], 'isActive': bool(entry['is_active']),
            'disabledReason': entry['disabled_reason'], 'protected': bool(entry['protected_from_sync']),
            'adoptedPatternId': entry['adopted_pattern_id'], 'lastMatchedAt': entry['last_matched_at'],
            'counts': counts}


@api.route('/community-segments', methods=['GET'])
@log_request
def list_community_segments():
    """Entries bound to one feed (?feed=<slug>)."""
    db = get_database()
    podcast = db.get_podcast_row(request.args.get('feed') or '')
    if not podcast:
        return error_response('feed not found', 404)
    entries = [e for e in seg.get_entries(db, bound_podcast_id=podcast['id']) if e['origin'] == 'community']
    counts = seg.event_counts(db, [e['id'] for e in entries])
    return json_response({'entries': [_public(e, counts[e['id']]) for e in entries]})


@api.route('/community-segments/summary', methods=['GET'])
@log_request
def community_segments_summary():
    return json_response(seg.summary(get_database()))


@api.route('/community-segments/settings', methods=['PUT'])
@log_request
def update_community_segments_settings():
    """Body: {enabled: bool}. The one switch of the feature; imports also need community sync on."""
    data = request.get_json(silent=True) or {}
    if not isinstance(data.get('enabled'), bool):
        return error_response('enabled must be true or false', 400)
    db = get_database()
    db.set_setting(seg.SETTING, 'true' if data['enabled'] else 'false')
    logger.info(f"community_show_segments set to {data['enabled']}")
    return json_response(seg.summary(db))


@api.route('/community-segments/<int:entry_id>/<action>', methods=['POST'])
@log_request
def act_on_community_segment(entry_id, action):
    if action not in _ACTIONS:
        return error_response(f'unknown action {action}', 404)
    db = get_database()
    entry = seg.set_state(db, entry_id, action)
    if entry is None:
        return error_response('entry not found', 404)
    return json_response(_public(entry, seg.event_counts(db, [entry_id])[entry_id]))


@api.route('/community-segments/<int:entry_id>', methods=['DELETE'])
@log_request
def delete_community_segment(entry_id):
    db = get_database()
    if seg.get_entry(db, entry_id) is None:
        return error_response('entry not found', 404)
    return json_response({'deleted': seg.delete_entries(db, id=entry_id)})


@api.route('/community-segments', methods=['DELETE'])
@log_request
def delete_all_community_segments():
    """Remove every imported entry (local exports are kept so a re-export keeps its id)."""
    db = get_database()
    return json_response({'deleted': seg.delete_entries(db, origin='community')})


@api.route('/community-segments/export', methods=['POST'])
@log_request
def export_community_segments():
    """Body: {patternIds: [int]}. Returns {bundle, rejected, files}: one seg-<id8>.json per entry, the files a PR
    adds under patterns/show-segments/."""
    ids = (request.get_json(silent=True) or {}).get('patternIds')
    if not isinstance(ids, list) or not ids or not all(isinstance(i, int) and not isinstance(i, bool) for i in ids):
        return error_response('patternIds must be a non-empty list of integers', 400)
    bundle, rejected = seg.build_segment_bundle(get_database(), ids)
    files = [{'filename': f"seg-{e['community_id'].replace('-', '')[:8]}.json", 'entry': e} for e in bundle['patterns']]
    return json_response({'bundle': bundle, 'rejected': rejected, 'files': files})
