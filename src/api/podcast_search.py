"""Podcast search routes: /podcast-search endpoint."""
import hashlib
import logging
import os
import time

import requests
from flask import request

from api import api, log_request, json_response, error_response, get_database, limiter
from config import HTTP_MAX_REDIRECTS_API, HTTP_TIMEOUT_API
from utils.safe_http import URLTrust, safe_get
from utils.url import SSRFError

logger = logging.getLogger('podcast.api')


def _get_podcast_index_credentials():
    """Resolve PodcastIndex credentials: DB first (decrypted), then env vars.

    Both podcast_index_api_key and podcast_index_api_secret live in
    SECRET_SETTING_KEYS and are stored encrypted under the master
    passphrase. Using `get_setting` here would hand the `enc:v1:...`
    ciphertext to the SHA-1 signer and produce a bogus X-Auth header;
    PodcastIndex then 401s.
    """
    db = get_database()
    api_key = db.get_secret('podcast_index_api_key') or os.environ.get('PODCAST_INDEX_API_KEY', '')
    api_secret = db.get_secret('podcast_index_api_secret') or os.environ.get('PODCAST_INDEX_API_SECRET', '')
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
    # PodcastIndex API requires SHA-1 for its X-Auth signature (upstream contract),
    # not a security-sensitive hash of secret material on our side. False positive.
    sha1_hash = hashlib.sha1(data_to_hash.encode('utf-8')).hexdigest()  # nosec B324 - required by PodcastIndex API

    headers = {
        'X-Auth-Key': api_key,
        'X-Auth-Date': str(epoch_time),
        'Authorization': sha1_hash,
        'User-Agent': 'MinusPod/1.0',
    }

    params = {'q': query, 'max': 10, 'fulltext': ''}
    qs = '&'.join(f"{k}={requests.utils.requote_uri(str(v))}" for k, v in params.items())
    endpoint = f"https://api.podcastindex.org/api/1.0/search/byterm?{qs}"
    try:
        resp = safe_get(
            endpoint,
            trust=URLTrust.OPERATOR_CONFIGURED,
            timeout=HTTP_TIMEOUT_API,
            max_redirects=HTTP_MAX_REDIRECTS_API,
            headers=headers,
        )
        resp.raise_for_status()
    except SSRFError as e:
        logger.warning(f"PodcastIndex SSRF block: {e}")
        return error_response('PodcastIndex endpoint rejected by SSRF validation', 502)
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
