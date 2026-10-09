"""Podping routes: /podping/* diagnostics for observed podping traffic."""
import json
import logging

from flask import request

from api import api, get_database, json_response, log_request
from config import PODPING_HOST_ACTIVE_DAYS
from podping_listener import (
    DEGRADED_SETTING, DEGRADED_SINCE_SETTING, PODPING_NODE_SETTING,
    PODPING_NODES, get_node_health_summary, get_podping_nodes,
    normalize_podping_nodes,
)

logger = logging.getLogger('podcast.api')


@api.route('/podping/nodes', methods=['GET'])
@log_request
def get_podping_node_settings():
    db = get_database()
    return json_response({
        'nodes': get_podping_nodes(db),
        'defaults': list(PODPING_NODES),
    })


@api.route('/podping/nodes', methods=['PUT'])
@log_request
def update_podping_node_settings():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or 'nodes' not in payload:
        return json_response({'error': 'nodes must be a list of HTTP(S) URLs'}, 400)
    try:
        nodes = normalize_podping_nodes(payload['nodes'])
    except ValueError as exc:
        return json_response({'error': str(exc)}, 400)
    db = get_database()
    db.set_setting(PODPING_NODE_SETTING, json.dumps(nodes))
    return json_response({'nodes': nodes, 'defaults': list(PODPING_NODES)})


@api.route('/podping/nodes/reset', methods=['POST'])
@log_request
def reset_podping_node_settings():
    db = get_database()
    db.clear_setting(PODPING_NODE_SETTING)
    return json_response({
        'nodes': list(PODPING_NODES),
        'defaults': list(PODPING_NODES),
    })


@api.route('/podping/hosts', methods=['GET'])
@log_request
def list_podping_hosts():
    """Domains seen sending podpings on the Hive chain, newest activity first.

    Counts are per domain rather than per notification; the listener aggregates
    as it goes, so there is no ping-by-ping history to return.
    """
    db = get_database()
    limit = min(max(1, request.args.get('limit', 100, type=int)), 500)
    # Compared per row rather than against a materialized set: the table is
    # attacker-influenced and can hold far more domains than one page.
    cutoff = db.podping_active_cutoff(PODPING_HOST_ACTIVE_DAYS)

    hosts = [{
        'domain': row['domain'],
        'firstSeenAt': row['first_seen_at'],
        'lastSeenAt': row['last_seen_at'],
        'pingCount': row['ping_count'],
        'active': (row['last_seen_at'] or '') >= cutoff,
    } for row in db.get_podping_hosts(limit)]

    return json_response({
        'hosts': hosts,
        'limit': limit,
        'totalDomains': db.count_podping_hosts(),
        'activeDomains': db.count_active_podping_domains(PODPING_HOST_ACTIVE_DAYS),
        'activeWindowDays': PODPING_HOST_ACTIVE_DAYS,
        'listenerEnabled': db.get_setting_bool('podping_enabled', False),
        # Non-fatal RPC-node health: the listener still falls back to RSS
        # polling, so this never gates feed refresh or /health.
        'allNodesDown': db.get_setting(DEGRADED_SETTING) == '1',
        'degradedSince': db.get_setting(DEGRADED_SINCE_SETTING) or None,
        'nodes': get_node_health_summary(db),
    })
