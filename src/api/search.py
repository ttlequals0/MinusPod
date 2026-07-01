"""Search routes: /search/* endpoints."""
import logging

from flask import request

from api import (
    api, limiter, log_request, json_response, error_response,
    get_database,
)

logger = logging.getLogger('podcast.api')


# ========== Search Endpoints ==========

@api.route('/search', methods=['GET'])
@log_request
def search():
    """Full-text search across all content.

    Query params:
        q: Search query (required)
        type: Filter by content type (episode, podcast, pattern, sponsor)
        limit: Maximum results (default 50, max 100)

    Returns:
        List of search results with type, id, podcastSlug, title, snippet, score
    """
    query = request.args.get('q', '').strip()
    if not query:
        return error_response('Search query (q) is required', 400)

    content_type = request.args.get('type')
    if content_type and content_type not in ('episode', 'podcast', 'pattern', 'sponsor'):
        return error_response('Invalid type. Use: episode, podcast, pattern, sponsor', 400)

    try:
        limit = max(1, min(int(request.args.get('limit', 50)), 100))
    except ValueError:
        limit = 50

    db = get_database()
    results = db.search(query, content_type=content_type, limit=limit)

    return json_response({
        'query': query,
        'results': results,
        'total': len(results)
    })


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
