"""Stats routes: /stats/* endpoints."""
import logging

from flask import request

from api import (
    api, log_request, json_response,
    get_database,
)

logger = logging.getLogger('podcast.api')


@api.route('/stats/dashboard', methods=['GET'])
@log_request
def get_dashboard_stats():
    """Get aggregate dashboard statistics with avg/min/max."""
    db = get_database()
    podcast_slug = request.args.get('podcast_slug')
    stats = db.get_dashboard_stats(podcast_slug=podcast_slug)
    return json_response(stats)


@api.route('/stats/by-day', methods=['GET'])
@log_request
def get_stats_by_day():
    """Get episode processing counts by day of week."""
    db = get_database()
    podcast_slug = request.args.get('podcast_slug')
    data = db.get_stats_by_day(podcast_slug=podcast_slug)
    return json_response({'days': data})


@api.route('/stats/by-podcast', methods=['GET'])
@log_request
def get_stats_by_podcast():
    """Get per-podcast aggregate stats."""
    db = get_database()
    data = db.get_stats_by_podcast()
    return json_response({'podcasts': data})
