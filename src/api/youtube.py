"""YouTube feed helpers: source preview, cookies, yt-dlp update, Data API key test."""
import difflib
import logging
import subprocess

from flask import request

from api import api, error_response, get_database, get_storage, json_response, limiter, log_request
from api.podcast_search import search_directory
from youtube import cookies, discovery, settings as yt_settings, urls, ytdlp

logger = logging.getLogger(__name__)
RSS_MATCH_LIMIT = 3
RSS_MATCH_THRESHOLD = 0.6
# Stable public channel id used only to exercise the key.
_KEY_PROBE_CHANNEL = 'UC_x5XG1OV2P6uZZ5FSM9Ttw'


def _rss_matches(title: str) -> list[dict]:
    try:
        results, _ = search_directory(title)
    except Exception as exc:
        logger.info(f"Directory lookup for YouTube preview skipped: {type(exc).__name__}")
        return []
    wanted = discovery.normalize_title(title)
    scored = []
    for r in results:
        score = difflib.SequenceMatcher(None, wanted, discovery.normalize_title(r.get('title', ''))).ratio()
        if score >= RSS_MATCH_THRESHOLD:
            scored.append((score, {'title': r.get('title', ''), 'feedUrl': r.get('feedUrl', ''),
                                   'artworkUrl': r.get('artworkUrl', '')}))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [m for _, m in scored[:RSS_MATCH_LIMIT]]


@api.route('/youtube/preview', methods=['POST'])
@limiter.limit('20 per minute')
@log_request
def preview_youtube_source():
    db = get_database()
    if not db.get_setting_bool('youtube_enabled', False):
        return json_response({'enabled': False, 'source': None, 'rssMatches': []})
    data = request.get_json(silent=True) or {}
    source = urls.parse(str(data.get('url') or ''))
    if source is None:
        return error_response('Not a YouTube playlist, channel or video URL', 400)
    try:
        info = discovery.resolve_source(db, source)
    except discovery.SourceNotFound as exc:
        return error_response(str(exc), 404)
    except (discovery.DiscoveryError, ytdlp.YtDlpNotInstalled) as exc:
        return error_response(f'Could not look up that YouTube source: {exc}', 502)
    canonical = urls.canonical_source_url(info.kind, info.id)
    existing = next((p['slug'] for p in db.get_podcast_feed_urls() if p['source_url'] == canonical), None)
    return json_response({
        'enabled': True,
        'source': {'kind': info.kind, 'id': info.id, 'title': info.title, 'description': info.description,
                   'author': info.author, 'artworkUrl': info.artwork_url, 'videoCount': info.video_count,
                   'alreadyAdded': existing},
        'rssMatches': _rss_matches(info.title),
    })


@api.route('/youtube/search', methods=['GET'])
@limiter.limit('20 per minute')
@log_request
def search_youtube_sources():
    if not get_database().get_setting_bool('youtube_enabled', False):
        return json_response({'enabled': False, 'results': []})
    query = (request.args.get('q') or '').strip()
    if not query:
        return error_response('Search query is required', 400)
    try:
        results = discovery.search_sources(get_database(), query, discovery.SEARCH_LIMIT)
    except (discovery.DiscoveryError, ytdlp.YtDlpNotInstalled) as exc:
        return error_response(f'Could not search YouTube: {exc}', 502)
    return json_response({'enabled': True, 'results': results})


@api.route('/youtube/test-key', methods=['POST'])
@limiter.limit('10 per minute')
@log_request
def test_youtube_key():
    data = request.get_json(silent=True) or {}
    key = str(data.get('apiKey') or '').strip() or yt_settings.api_key(get_database())
    if not key:
        return error_response('No API key to test', 400)
    try:
        body = discovery._api_get('channels', {'part': 'snippet', 'id': _KEY_PROBE_CHANNEL}, key)
    except discovery.QuotaExceeded as exc:
        return error_response(str(exc), 400)
    except discovery.SourceNotFound:
        return error_response('YouTube accepted the key but the test channel was not found', 400)
    except discovery.DiscoveryError as exc:
        return error_response(f'YouTube rejected the request: {exc}', 400)
    items = body.get('items') or []
    return json_response({'ok': True, 'channelTitle': items[0]['snippet']['title'] if items else None})


@api.route('/youtube/cookies', methods=['POST'])
@log_request
def put_youtube_cookies():
    data = request.get_json(silent=True) or {}
    try:
        return json_response(cookies.save(get_storage(), str(data.get('cookies') or '')))
    except cookies.InvalidCookiesError as exc:
        return error_response(str(exc), 400)


@api.route('/youtube/cookies', methods=['DELETE'])
@log_request
def delete_youtube_cookies():
    storage = get_storage()
    cookies.clear(storage)
    return json_response(cookies.status(storage))


@api.route('/youtube/ytdlp/update', methods=['POST'])
@limiter.limit('6 per hour')
@log_request
def update_ytdlp_now():
    try:
        return json_response(ytdlp.update_binary(get_storage(), get_database()))
    except ytdlp.YtDlpNotInstalled as exc:
        return error_response(str(exc), 503)
    except subprocess.TimeoutExpired:
        return error_response('yt-dlp update timed out', 504)
