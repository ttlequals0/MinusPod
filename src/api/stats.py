"""Stats routes: /stats/* endpoints."""
import logging
import math

from flask import request

from api import (
    api, log_request, json_response,
    get_database,
)

logger = logging.getLogger('podcast.api')


def _parse_list_params():
    """Shared page/limit/sortBy/sortDir/filter parsing for the ledger list
    endpoints below. Mirrors GET /history's page+limit contract."""
    limit = min(max(1, request.args.get('limit', 50, type=int)), 100)
    page = max(1, request.args.get('page', 1, type=int))
    return {
        'page': page,
        'limit': limit,
        'sort_by': request.args.get('sortBy', ''),
        'sort_dir': request.args.get('sortDir', 'desc'),
        'from_date': request.args.get('from'),
        'to_date': request.args.get('to'),
        'podcast_slug': request.args.get('podcastSlug'),
        'provider': request.args.get('provider'),
        'model': request.args.get('model'),
    }


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


@api.route('/stats/reviewer', methods=['GET'])
@log_request
def get_reviewer_stats():
    """Aggregate ad reviewer stats.

    Optional filters: ``podcast_slug`` and ``episode_id``. Without either,
    returns global aggregates over the ad_reviewer_log table.
    """
    db = get_database()
    podcast_slug = request.args.get('podcast_slug')
    episode_id = request.args.get('episode_id')
    return json_response(db.get_reviewer_stats(
        podcast_slug=podcast_slug, episode_id=episode_id
    ))


@api.route('/stats/addressing', methods=['GET'])
@log_request
def get_addressing_stats():
    """Per-addressing-mode LLM contract compliance aggregates."""
    db = get_database()
    podcast_slug = request.args.get('podcast_slug')
    return json_response(db.get_addressing_stats(podcast_slug=podcast_slug))


@api.route('/stats/model-usage', methods=['GET'])
@log_request
def get_model_usage_stats():
    """Paginated spend by (provider, model) over the llm_call_usage ledger."""
    db = get_database()
    params = _parse_list_params()
    items, total = db.get_model_usage_stats(**params)
    return json_response({
        'items': items,
        'total': total,
        'totalPages': math.ceil(total / params['limit']) if total > 0 else 1,
        'page': params['page'],
        'limit': params['limit'],
    })


@api.route('/stats/episode-costs', methods=['GET'])
@log_request
def get_episode_cost_stats():
    """Paginated per-episode cost breakdown over the llm_call_usage ledger."""
    db = get_database()
    params = _parse_list_params()
    items, total = db.get_episode_cost_stats(**params)
    return json_response({
        'items': items,
        'total': total,
        'totalPages': math.ceil(total / params['limit']) if total > 0 else 1,
        'page': params['page'],
        'limit': params['limit'],
    })
