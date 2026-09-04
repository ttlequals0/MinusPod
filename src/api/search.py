"""Search routes: /search/* endpoints."""
import logging

from flask import request

from api import (
    api, limiter, log_request, json_response, error_response,
    get_database,
)
from api.settings import _clamped_int
from utils.constants import EpisodeStatus

logger = logging.getLogger('podcast.api')


# ========== Search Endpoints ==========

@api.route('/search', methods=['GET'])
@log_request
def search():
    """Unified search: {query, shows, episodes, transcripts, patterns, sponsors}, covering episodes of any status."""
    query = request.args.get('q', '').strip()
    if not query:
        return error_response('Search query (q) is required', 400)

    limit = _clamped_int(request.args.get('limit'), 50, 1, 100)

    result = get_database().search_grouped(query, limit=limit)
    for ep in result['episodes']:
        ep['status'] = EpisodeStatus.to_api(ep['status'])

    return json_response({'query': query, **result})


@api.route('/search/rebuild', methods=['POST'])
@limiter.limit("1 per minute")
@log_request
def rebuild_search_index():
    """Rebuild the full-text search index.

    This reindexes all content (podcasts, episodes, patterns, sponsors).
    May take a few seconds for large databases.
    """
    db = get_database()
    count = db.rebuild_search_index()

    return json_response({
        'message': f'Search index rebuilt with {count} items',
        'indexedCount': count
    })


@api.route('/search/stats', methods=['GET'])
@log_request
def search_stats():
    """Get search index statistics."""
    db = get_database()
    stats = db.get_search_index_stats()

    return json_response({
        'stats': stats
    })
