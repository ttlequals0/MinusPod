"""Podcast search routes: /podcast-search endpoint."""
import hashlib
import logging
import os
import time

import requests
from flask import request

from api import api, log_request, json_response, error_response, get_database, limiter
from config import HTTP_MAX_REDIRECTS_API, HTTP_TIMEOUT_API
from utils.connection_probe import run_probe, parse_probe_json, probe_snippet
from utils.safe_http import URLTrust, safe_get
from utils.url import SSRFError

logger = logging.getLogger('podcast.api')

# One URL builder for the real search and the connection test: a passing
# test only implies working search while both hit the same endpoint.
_SEARCH_BYTERM_URL = 'https://api.podcastindex.org/api/1.0/search/byterm'


def _podcast_index_headers(api_key: str, api_secret: str) -> dict:
    """Signed auth headers for a PodcastIndex request. Shared by the real
    search route and the connection test so the two cannot drift."""
    epoch_time = int(time.time())
    data_to_hash = api_key + api_secret + str(epoch_time)
    # PodcastIndex API requires SHA-1 for its X-Auth signature (upstream contract),
    # not a security-sensitive hash of secret material on our side. False positive.
    sha1_hash = hashlib.sha1(data_to_hash.encode('utf-8')).hexdigest()  # nosec B324 - required by PodcastIndex API
    return {
        'X-Auth-Key': api_key,
        'X-Auth-Date': str(epoch_time),
        'Authorization': sha1_hash,
        'User-Agent': 'MinusPod/1.0',
    }


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

    headers = _podcast_index_headers(api_key, api_secret)

    params = {'q': query, 'max': 10, 'fulltext': ''}
    qs = '&'.join(f"{k}={requests.utils.requote_uri(str(v))}" for k, v in params.items())
    endpoint = f"{_SEARCH_BYTERM_URL}?{qs}"
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


@api.route('/settings/podcast-index/test', methods=['POST'])
@log_request
@limiter.limit("10 per minute")
def test_podcast_index():
    """Connection test for the configured PodcastIndex credentials (#544).

    Sends the same signed request the real podcast search uses (a one-result
    search), so a passing test means search will work. Uses only the saved
    credentials; drafts in the form must be saved first.
    """
    api_key, api_secret = _get_podcast_index_credentials()
    if not api_key or not api_secret:
        return json_response(
            {'ok': False, 'reachable': False,
             'detail': 'Enter and save the API key and secret first.'}, 200)

    headers = _podcast_index_headers(api_key, api_secret)
    url = f'{_SEARCH_BYTERM_URL}?q=test&max=1'
    error, status, body_bytes = run_probe(
        lambda: safe_get(
            url,
            trust=URLTrust.OPERATOR_CONFIGURED,
            timeout=HTTP_TIMEOUT_API,
            max_redirects=HTTP_MAX_REDIRECTS_API,
            headers=headers,
            stream=True,
        ),
        HTTP_TIMEOUT_API,
        log_context='api.podcastindex.org',
    )
    if error:
        return json_response(error, 200)

    result = {'ok': False, 'reachable': True, 'status': status}
    if status < 400 and parse_probe_json(body_bytes) is not None:
        result['ok'] = True
        result['detail'] = (f'Connected. PodcastIndex accepted the '
                            f'credentials (HTTP {status}).')
    elif status in (401, 403):
        result['detail'] = (f'PodcastIndex rejected the credentials '
                            f'(HTTP {status}). Check the API key and secret.')
    else:
        snippet = probe_snippet(body_bytes)
        result['detail'] = (f'PodcastIndex returned HTTP {status}'
                            + (f': {snippet}' if snippet else '.'))
    return json_response(result, 200)
