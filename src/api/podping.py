"""Podping routes: /podping/* diagnostics for observed podping traffic."""
import logging

from flask import request

from api import api, get_database, json_response, log_request
from config import PODPING_HOST_ACTIVE_DAYS

logger = logging.getLogger('podcast.api')


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
    })
