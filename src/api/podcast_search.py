"""Podcast search routes: /podcast-search endpoint."""
import hashlib
import logging
import os
import time

import requests
from flask import request

from api import api, log_request, json_response, error_response, get_database, limiter

logger = logging.getLogger('podcast.api')


def _get_podcast_index_credentials():
    """Resolve PodcastIndex credentials: DB first, then env vars."""
    db = get_database()
    api_key = db.get_setting('podcast_index_api_key') or os.environ.get('PODCAST_INDEX_API_KEY', '')
    api_secret = db.get_setting('podcast_index_api_secret') or os.environ.get('PODCAST_INDEX_API_SECRET', '')
    return api_key, api_secret


@api.route('/podcast-search', methods=['GET'])
@log_request
@limiter.limit("30 per minute")
def search_podcasts():
    """Search for podcasts via PodcastIndex.org API."""
    query = request.args.get('q', '').strip()
    if not query:
        return error_response('Query parameter "q" is required', 400)

    api_key, api_secret = _get_podcast_index_credentials()
    if not api_key or not api_secret:
        return error_response(
            'PodcastIndex API credentials not configured. '
            'Set them in Settings or via PODCAST_INDEX_API_KEY/PODCAST_INDEX_API_SECRET environment variables.',
            503,
        )

    # PodcastIndex auth header generation
    epoch_time = int(time.time())
    data_to_hash = api_key + api_secret + str(epoch_time)
    sha1_hash = hashlib.sha1(data_to_hash.encode('utf-8')).hexdigest()

    headers = {
        'X-Auth-Key': api_key,
        'X-Auth-Date': str(epoch_time),
        'Authorization': sha1_hash,
        'User-Agent': 'MinusPod/1.0',
    }

    try:
        resp = requests.get(
            'https://api.podcastindex.org/api/1.0/search/byterm',
            params={'q': query, 'max': 10, 'fulltext': ''},
            headers=headers,
            timeout=10,
        )
        resp.raise_for_status()
    except requests.exceptions.Timeout:
        return error_response('PodcastIndex API request timed out', 502)
    except requests.exceptions.RequestException as e:
        logger.error(f"PodcastIndex API error: {e}")
        return error_response('Failed to reach PodcastIndex API', 502)

    try:
        data = resp.json()
    except ValueError:
        logger.error("PodcastIndex returned non-JSON response")
        return error_response('PodcastIndex returned an invalid response', 502)

    feeds = data.get('feeds', [])

    results = []
    for feed in feeds:
        results.append({
            'id': feed.get('id'),
            'title': feed.get('title', ''),
            'description': feed.get('description', ''),
            'artworkUrl': feed.get('artwork') or feed.get('image') or '',
            'feedUrl': feed.get('url', ''),
            'author': feed.get('author', ''),
            'link': feed.get('link', ''),
        })

    return json_response({'results': results})
