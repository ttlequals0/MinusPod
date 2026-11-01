"""List videos for a YouTube feed through the Data API or yt-dlp."""
import difflib
import json
import logging
import re
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import quote_plus, urlencode

import requests

from config import HTTP_TIMEOUT_API
from rss_parser import RSSParser
from utils.app_version import APP_VERSION
from utils.safe_http import URLTrust, read_response_capped, safe_get
from utils.time import epoch_to_iso
from utils.ttl_cache import TTLCache
from utils.url import SSRFError
from utils.youtube_fields import PLACEHOLDER_TITLES
from youtube import download, settings as yt_settings, ytdlp
from youtube.urls import Source, parse as parse_source, source_url, uploads_playlist, watch_url

logger = logging.getLogger(__name__)

API = 'https://www.googleapis.com/youtube/v3'
API_MAX_BYTES = 2 * 1024 * 1024
SHORTS_MAX_SECONDS = 180
PLAYLIST_SCAN_CAP = 5000
UNAVAILABLE_SLACK = 20
_UNAVAILABLE_TITLES = frozenset(PLACEHOLDER_TITLES)
_ISO_DURATION = re.compile(r'^P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$')
SEARCH_FETCH = 12
SEARCH_CHANNEL_LOOKUPS = 3
SEARCH_LIMIT = 8
SEARCH_PODCAST_TAB_CAP = 20
PODCAST_BONUS = 0.25
_SEARCH_FILTER = 'EgIQAw%3D%3D'
_ids = RSSParser()
_search_cache = TTLCache(ttl_seconds=600, max_size=64)
_podcast_tab_cache = TTLCache(ttl_seconds=3600, max_size=256)
_search_lock = threading.Lock()
_inflight: dict[tuple[str, bool], threading.Lock] = {}
_MISS = object()


class DiscoveryError(Exception):
    pass


class SourceNotFound(DiscoveryError):
    pass


class QuotaExceeded(DiscoveryError):
    pass


@dataclass(frozen=True)
class SourceInfo:
    kind: str
    id: str
    title: str
    description: str
    artwork_url: str | None
    video_count: int | None
    author: str = ''


def parse_iso_duration(text) -> float | None:
    if not text:
        return None
    m = _ISO_DURATION.match(text)
    if not m:
        return None
    days, hours, minutes, seconds = (int(x or 0) for x in m.groups())
    return float(days * 86400 + hours * 3600 + minutes * 60 + seconds)


_LIVE_STATUSES = ('is_live', 'is_upcoming', 'post_live')


def _api_get(path: str, params: dict, key: str, *, proxy: str = '') -> dict:
    url = f"{API}/{path}?{urlencode(params)}"
    headers = {'User-Agent': f'MinusPod/{APP_VERSION}', 'X-Goog-Api-Key': key}
    # Error text may embed the request URL, so messages carry only the exception type.
    try:
        response = safe_get(url, URLTrust.OPERATOR_CONFIGURED, timeout=HTTP_TIMEOUT_API, stream=True,
                            headers=headers, proxies={'http': proxy, 'https': proxy} if proxy else None)
    except (requests.RequestException, SSRFError) as exc:
        raise DiscoveryError(f'YouTube Data API request failed ({type(exc).__name__})') from None
    try:
        status = response.status_code
        body = read_response_capped(response, API_MAX_BYTES) if status == 200 else b''
        text = response.text or '' if status != 200 else ''
    except (requests.RequestException, ValueError) as exc:
        raise DiscoveryError(f'YouTube Data API read failed ({type(exc).__name__})') from None
    finally:
        response.close()
    if status == 403 and 'quotaExceeded' in text:
        raise QuotaExceeded('YouTube Data API quota exceeded')
    if status == 404:
        raise SourceNotFound('Playlist or channel not found')
    if status != 200:
        raise DiscoveryError(f'YouTube Data API error {status}')
    try:
        return json.loads(body.decode('utf-8'))
    except ValueError:
        raise DiscoveryError('YouTube Data API returned an unreadable response') from None


def _ytdlp_json(url: str, extra: list[str], timeout: int) -> dict:
    try:
        result = ytdlp.run(['--flat-playlist', '--dump-single-json', '--no-warnings', *download.auth_args(), *extra, url], timeout=timeout)
    except subprocess.TimeoutExpired:
        raise DiscoveryError('yt-dlp timed out listing the source') from None
    except ytdlp.YtDlpNotInstalled as exc:
        raise DiscoveryError(str(exc)) from None
    if result.returncode != 0:
        error = ytdlp.trimmed_error(result.stderr)
        low = error.lower()
        if 'does not exist' in low or 'private' in low or 'unavailable' in low or 'not found' in low:
            raise SourceNotFound(error)
        raise DiscoveryError(error)
    try:
        return json.loads(result.stdout)
    except ValueError as exc:
        raise DiscoveryError('yt-dlp returned unreadable JSON') from exc


def _best_thumb(thumbs) -> str | None:
    if isinstance(thumbs, dict):
        for key in ('maxres', 'standard', 'high', 'medium', 'default'):
            if thumbs.get(key, {}).get('url'):
                return thumbs[key]['url']
        return None
    if isinstance(thumbs, list) and thumbs:
        return (thumbs[-1] or {}).get('url')
    return None


def resolve_source(db, source: Source) -> SourceInfo:
    key = yt_settings.api_key(db)
    if key:
        try:
            return _resolve_with_api(key, source, proxy=ytdlp.configured_proxy(db))
        except DiscoveryError as exc:
            logger.warning(f'YouTube Data API failed ({type(exc).__name__}); resolving with yt-dlp')
    if source.kind == 'video':
        data = _ytdlp_json(watch_url(source.id), ['--no-playlist'], timeout=120)
        return SourceInfo('video', source.id, data.get('title') or source.id, data.get('description') or '',
                          _best_thumb(data.get('thumbnails')), 1,
                          author=data.get('channel') or data.get('uploader') or '')
    data = _ytdlp_json(source_url(source), ['--playlist-items', '0'], timeout=120)
    if source.kind == 'playlist':
        return SourceInfo('playlist', source.id, data.get('title') or source.id, data.get('description') or '',
                          _best_thumb(data.get('thumbnails')), data.get('playlist_count'),
                          author=data.get('channel') or data.get('uploader') or '')
    channel_id = data.get('channel_id') or data.get('uploader_id')
    if not channel_id or not channel_id.startswith('UC'):
        raise SourceNotFound('Channel not found')
    title = data.get('channel') or data.get('title') or ''
    return SourceInfo('channel', channel_id, title or channel_id,
                      data.get('description') or '', _best_thumb(data.get('thumbnails')), data.get('playlist_count'),
                      author=title)


def _resolve_with_api(key: str, source: Source, *, proxy: str = '') -> SourceInfo:
    try:
        return _parse_api_source(key, source, proxy=proxy)
    except (KeyError, IndexError, TypeError, AttributeError):
        raise DiscoveryError('YouTube Data API returned an unexpected payload') from None


def _parse_api_source(key: str, source: Source, *, proxy: str = '') -> SourceInfo:
    if source.kind == 'video':
        data = _api_get('videos', {'part': 'snippet', 'id': source.id}, key, proxy=proxy)
        items = data.get('items') or []
        if not items:
            raise SourceNotFound('Video not found or private')
        sn = items[0]['snippet']
        return SourceInfo('video', source.id, sn.get('title') or source.id, sn.get('description') or '',
                          _best_thumb(sn.get('thumbnails')), 1, author=sn.get('channelTitle') or '')
    if source.kind == 'playlist':
        data = _api_get('playlists', {'part': 'snippet,contentDetails', 'id': source.id}, key, proxy=proxy)
        items = data.get('items') or []
        if not items:
            raise SourceNotFound('Playlist not found or private')
        sn = items[0]['snippet']
        return SourceInfo('playlist', source.id, sn.get('title') or source.id, sn.get('description') or '',
                          _best_thumb(sn.get('thumbnails')), items[0].get('contentDetails', {}).get('itemCount'),
                          author=sn.get('channelTitle') or '')
    selector = {'channel': 'id', 'handle': 'forHandle', 'user': 'forUsername', 'custom': 'forHandle'}[source.kind]
    data = _api_get('channels', {'part': 'snippet,statistics', selector: source.id}, key, proxy=proxy)
    items = data.get('items') or []
    if not items:
        raise SourceNotFound('Channel not found')
    sn = items[0]['snippet']
    count = items[0].get('statistics', {}).get('videoCount')
    return SourceInfo('channel', items[0]['id'], sn.get('title') or items[0]['id'], sn.get('description') or '',
                      _best_thumb(sn.get('thumbnails')), int(count) if count is not None else None,
                      author=sn.get('title') or '')


def _playlist_for(podcast: dict, include_shorts: bool) -> str:
    kind = podcast.get('youtube_source_kind')
    if kind == 'channel':
        return uploads_playlist(podcast['youtube_source_id'], include_shorts)
    if kind == 'playlist':
        return podcast['youtube_source_id']
    raise ValueError(f"unsupported YouTube source kind: {kind}")


def _episode(video_id: str, title: str, description: str, published_iso: str, duration, thumb) -> dict:
    url = watch_url(video_id)
    return {
        'id': _ids.generate_episode_id(url, f'yt:{video_id}'),
        'video_id': video_id, 'url': url, 'title': title or video_id,
        'published': published_iso, 'description': description or '', 'artwork_url': thumb,
        'episode_number': None, 'rss_duration': duration,
        'upstream_chapters_url': None, 'upstream_transcript_url': None, 'upstream_transcript_type': None,
        'tags': [],
    }


def _keep(duration, is_live_or_upcoming: bool, include_shorts: bool, from_channel_longform: bool) -> bool:
    if is_live_or_upcoming:
        return False
    if include_shorts or from_channel_longform:
        return True
    return duration is None or duration > SHORTS_MAX_SECONDS


def list_videos(db, podcast: dict, limit: int, settings=None) -> list[dict]:
    kind = podcast.get('youtube_source_kind')
    if kind == 'video':
        return _list_single_video(db, podcast)
    include_shorts = bool(podcast.get('youtube_include_shorts'))
    playlist_id = _playlist_for(podcast, include_shorts)
    longform = kind == 'channel' and not include_shorts
    # Uploads playlists are newest-first, so only a user playlist needs the whole list sorted.
    full = kind == 'playlist'
    hide_private = (settings or yt_settings.resolve_feed(db, podcast)).hide_private
    key = yt_settings.api_key(db)
    if key:
        try:
            episodes = _list_with_api(key, playlist_id, limit, include_shorts, longform, full,
                                      hide_private=hide_private, proxy=ytdlp.configured_proxy(db))
            logger.info(f"[{podcast.get('slug')}] Listed {len(episodes)} video(s) via the YouTube Data API")
            return episodes
        except DiscoveryError as exc:
            logger.warning(f"[{podcast.get('slug')}] YouTube Data API failed ({type(exc).__name__}); "
                           "listing with yt-dlp")
    return _list_with_ytdlp(playlist_id, limit, include_shorts, longform, full, hide_private=hide_private)


def _list_single_video(db, podcast: dict) -> list[dict]:
    video_id = podcast['youtube_source_id']
    key = yt_settings.api_key(db)
    if key:
        try:
            return _single_video_from_api(key, video_id, proxy=ytdlp.configured_proxy(db))
        except DiscoveryError as exc:
            logger.warning(f"[{podcast.get('slug')}] YouTube Data API failed ({type(exc).__name__}); "
                           "listing with yt-dlp")
    data = _ytdlp_json(watch_url(video_id), ['--no-playlist'], timeout=120)
    duration = float(data['duration']) if data.get('duration') is not None else None
    if not _keep(duration, data.get('live_status') in _LIVE_STATUSES, True, False):
        return []
    return [_episode(video_id, data.get('title'), data.get('description'), _published_from_entry(data),
                     duration, _best_thumb(data.get('thumbnails')))]


def _single_video_from_api(key: str, video_id: str, *, proxy: str = '') -> list[dict]:
    try:
        data = _api_get('videos', {'part': 'contentDetails,snippet,liveStreamingDetails', 'id': video_id}, key,
                        proxy=proxy)
        episodes = []
        for item in data.get('items') or []:
            live = item.get('liveStreamingDetails') or {}
            duration = parse_iso_duration(item.get('contentDetails', {}).get('duration'))
            if not _keep(duration, bool(live) and not live.get('actualEndTime'), True, False):
                continue
            sn = item.get('snippet', {})
            episodes.append(_episode(item['id'], sn.get('title'), sn.get('description'),
                                     sn.get('publishedAt') or '', duration, _best_thumb(sn.get('thumbnails'))))
        return episodes
    except (KeyError, IndexError, TypeError, AttributeError):
        raise DiscoveryError('YouTube Data API returned an unexpected payload') from None


def _list_with_api(key, playlist_id, limit, include_shorts, longform, full=False, *, hide_private,
                   proxy='') -> list[dict]:
    try:
        return _parse_api_listing(key, playlist_id, limit, include_shorts, longform, full, hide_private=hide_private,
                                  proxy=proxy)
    except (KeyError, IndexError, TypeError, AttributeError):
        raise DiscoveryError('YouTube Data API returned an unexpected payload') from None


def _parse_api_listing(key, playlist_id, limit, include_shorts, longform, full=False, *, hide_private,
                       proxy='') -> list[dict]:
    items: list[tuple[str, str]] = []
    token = None
    while True:
        params = {'part': 'contentDetails', 'playlistId': playlist_id, 'maxResults': 50}
        if token:
            params['pageToken'] = token
        page = _api_get('playlistItems', params, key, proxy=proxy)
        items.extend((i['contentDetails']['videoId'], i['contentDetails'].get('videoPublishedAt') or '')
                     for i in page.get('items') or [])
        token = page.get('nextPageToken')
        if not token or len(items) >= (PLAYLIST_SCAN_CAP if full else limit + UNAVAILABLE_SLACK):
            break
    if full:
        items.sort(key=lambda item: item[1], reverse=True)
    ids = [video_id for video_id, _ in items][:limit + UNAVAILABLE_SLACK]
    published_by_id = dict(items)
    by_id = {}
    returned = set()
    for start in range(0, len(ids), 50):
        chunk = ids[start:start + 50]
        data = _api_get('videos', {'part': 'contentDetails,snippet,liveStreamingDetails', 'id': ','.join(chunk)}, key,
                        proxy=proxy)
        for item in data.get('items') or []:
            returned.add(item['id'])
            live = item.get('liveStreamingDetails') or {}
            is_live = bool(live) and not live.get('actualEndTime')
            duration = parse_iso_duration(item.get('contentDetails', {}).get('duration'))
            if not _keep(duration, is_live, include_shorts, longform):
                continue
            sn = item.get('snippet', {})
            by_id[item['id']] = _episode(item['id'], sn.get('title'), sn.get('description'),
                                         sn.get('publishedAt') or '', duration, _best_thumb(sn.get('thumbnails')))
    episodes = []
    for vid in ids:
        if vid in by_id:
            episodes.append(by_id[vid])
        elif not hide_private and vid not in returned:
            # videos.list omits private and deleted ids
            episodes.append(_episode(vid, PLACEHOLDER_TITLES['[Private video]'], '', published_by_id.get(vid) or '', None, None))
    return episodes[:limit]


def _published_from_entry(entry: dict) -> str:
    if entry.get('timestamp'):
        return epoch_to_iso(int(entry['timestamp']))
    raw = entry.get('upload_date') or entry.get('release_date')
    if raw and len(raw) == 8:
        return datetime(int(raw[:4]), int(raw[4:6]), int(raw[6:]), tzinfo=timezone.utc).isoformat()
    return ''


def _list_with_ytdlp(playlist_id, limit, include_shorts, longform, full=False, *, hide_private) -> list[dict]:
    extra = ['--extractor-args', 'youtubetab:approximate_date']
    if not full:
        extra = ['--playlist-end', str(limit + UNAVAILABLE_SLACK), *extra]
    data = _ytdlp_json(f'https://www.youtube.com/playlist?list={playlist_id}', extra, timeout=180)
    # Private or deleted videos come back as a bare id or a placeholder title.
    entries = [e for e in data.get('entries') or [] if e and e.get('id')]
    if hide_private:
        entries = [e for e in entries if e.get('title') and e['title'] not in _UNAVAILABLE_TITLES]
    if full:
        entries.sort(key=_published_from_entry, reverse=True)
    entries = entries[:limit]
    episodes = []
    for entry in entries:
        is_live = entry.get('live_status') in _LIVE_STATUSES
        duration = float(entry['duration']) if entry.get('duration') is not None else None
        if not _keep(duration, is_live, include_shorts, longform):
            continue
        title = PLACEHOLDER_TITLES.get(entry.get('title')) or entry.get('title') or PLACEHOLDER_TITLES['[Private video]']
        episodes.append(_episode(entry['id'], title, entry.get('description'),
                                 _published_from_entry(entry), duration, _best_thumb(entry.get('thumbnails'))))
    return episodes


def normalize_title(text: str) -> str:
    return re.sub(r'[^a-z0-9 ]+', ' ', (text or '').lower()).strip()


def _candidate(kind, source_id, title, channel_title, video_count, podcast, artwork) -> dict:
    return {'kind': kind, 'id': source_id, 'title': title or source_id,
            'channelTitle': channel_title or (title if kind == 'channel' else '') or '',
            'videoCount': video_count, 'podcast': podcast, 'artworkUrl': artwork,
            'url': source_url(Source(kind, source_id))}


def _rank(candidates: list[dict], query: str) -> list[dict]:
    wanted = normalize_title(query)

    def sort_key(c):
        ratio = difflib.SequenceMatcher(None, wanted, normalize_title(c['title'])).ratio()
        return (-(ratio + (PODCAST_BONUS if c['podcast'] is True else 0.0)), c['kind'] != 'playlist')
    return sorted(candidates, key=sort_key)


def _api_rows(found: dict) -> list[tuple[str, str, dict]]:
    try:
        rows = []
        for item in found.get('items') or []:
            ident = item['id']
            kind, source_id = ('playlist', ident['playlistId']) if ident.get('playlistId') else ('channel', ident['channelId'])
            rows.append((kind, source_id, item['snippet']))
        return rows
    except (KeyError, TypeError, AttributeError):
        raise DiscoveryError('YouTube Data API returned an unexpected payload') from None


def _api_candidates(rows, detail_items) -> tuple[dict, dict]:
    try:
        details = {p['id']: p for p in detail_items}
        candidates: dict[tuple[str, str], dict] = {}
        owners: dict[str, str] = {}
        for kind, source_id, sn in rows:
            podcast = count = None
            if kind == 'playlist':
                detail = details.get(source_id) or {}
                status = (detail.get('status') or {}).get('podcastStatus')
                podcast = None if status in (None, 'unspecified') else status == 'enabled'
                count = (detail.get('contentDetails') or {}).get('itemCount')
                if sn.get('channelId'):
                    owners[source_id] = sn['channelId']
            candidates[(kind, source_id)] = _candidate(
                kind, source_id, sn.get('title'), sn.get('channelTitle'),
                int(count) if count is not None else None, podcast, _best_thumb(sn.get('thumbnails')))
        return candidates, owners
    except (KeyError, TypeError, AttributeError, ValueError):
        raise DiscoveryError('YouTube Data API returned an unexpected payload') from None


def _search_with_api(key: str, query: str, *, proxy: str = '') -> tuple[dict, dict]:
    found = _api_get('search', {'part': 'snippet', 'q': query, 'type': 'playlist,channel',
                                'maxResults': SEARCH_FETCH}, key, proxy=proxy)
    rows = _api_rows(found)
    playlist_ids = [i for k, i, _ in rows if k == 'playlist']
    detail_items = []
    if playlist_ids:
        data = _api_get('playlists', {'part': 'snippet,status,contentDetails', 'id': ','.join(playlist_ids)},
                        key, proxy=proxy)
        detail_items = data.get('items') or []
    return _api_candidates(rows, detail_items)


def _channel_podcasts(channel_id: str) -> list[dict] | None:
    """Podcasts-tab entries, or None when the tab is missing or the lookup failed (deliberately conflated)."""
    with _search_lock:
        cached = _podcast_tab_cache.get(channel_id, _MISS)
    if cached is not _MISS:
        return cached
    try:
        data = _ytdlp_json(f'https://www.youtube.com/channel/{channel_id}/podcasts',
                           ['--playlist-end', str(SEARCH_PODCAST_TAB_CAP)], timeout=45)
        entries = [e for e in data.get('entries') or [] if e and e.get('id')]
    except DiscoveryError:
        entries = None
    with _search_lock:
        _podcast_tab_cache.set(channel_id, entries)
    return entries


def _expand_channel_podcasts(candidates: dict, owners: dict) -> None:
    channel_ids = list(dict.fromkeys(
        [i for k, i in candidates if k == 'channel'] + list(owners.values())))[:SEARCH_CHANNEL_LOOKUPS]
    if not channel_ids:
        return
    with ThreadPoolExecutor(max_workers=SEARCH_CHANNEL_LOOKUPS) as pool:
        lookups = dict(zip(channel_ids, pool.map(_channel_podcasts, channel_ids), strict=True))
    listed = set()
    for channel_id, entries in lookups.items():
        channel = candidates.get(('channel', channel_id))
        for entry in entries or []:
            listed.add(entry['id'])
            if ('playlist', entry['id']) not in candidates:
                candidates[('playlist', entry['id'])] = _candidate(
                    'playlist', entry['id'], entry.get('title'),
                    entry.get('channel') or entry.get('uploader') or (channel or {}).get('title'),
                    entry.get('playlist_count'), True, _best_thumb(entry.get('thumbnails')))
    for (kind, source_id), cand in candidates.items():
        if kind != 'playlist':
            continue
        tab = lookups.get(owners.get(source_id))
        if source_id in listed:
            cand['podcast'] = True
        elif tab is not None and len(tab) < SEARCH_PODCAST_TAB_CAP:
            cand['podcast'] = False


def _search_with_ytdlp(query: str) -> tuple[dict, dict]:
    url = f'https://www.youtube.com/results?search_query={quote_plus(query)}&sp={_SEARCH_FILTER}'
    data = _ytdlp_json(url, ['--playlist-end', str(SEARCH_FETCH)], timeout=60)
    candidates: dict[tuple[str, str], dict] = {}
    owners: dict[str, str] = {}
    for entry in data.get('entries') or []:
        source = parse_source(str((entry or {}).get('id') or ''))
        if source is None or source.kind not in ('playlist', 'channel'):
            continue
        owner = entry.get('channel_id') or entry.get('uploader_id') or ''
        if source.kind == 'playlist' and owner.startswith('UC'):
            owners[source.id] = owner
        candidates.setdefault((source.kind, source.id), _candidate(
            source.kind, source.id, entry.get('title'), entry.get('channel') or entry.get('uploader'),
            entry.get('playlist_count'), None, _best_thumb(entry.get('thumbnails'))))
    return candidates, owners


def search_sources(db, query: str, limit: int = SEARCH_LIMIT) -> list[dict]:
    q = ' '.join((query or '').split())
    key = yt_settings.api_key(db)
    cache_key = (normalize_title(q), bool(key))
    with _search_lock:
        ranked = _search_cache.get(cache_key)
        if ranked is None:
            lock = _inflight.setdefault(cache_key, threading.Lock())
    if ranked is not None:
        return ranked[:limit]
    with lock:
        try:
            with _search_lock:
                ranked = _search_cache.get(cache_key)
            if ranked is None:
                found = None
                if key:
                    try:
                        found = _search_with_api(key, q, proxy=ytdlp.configured_proxy(db))
                    except DiscoveryError as exc:
                        logger.warning(f'YouTube Data API failed ({type(exc).__name__}); searching with yt-dlp')
                candidates, owners = found if found is not None else _search_with_ytdlp(q)
                # API results already carry podcast status; only channel hits need the tab.
                _expand_channel_podcasts(candidates, {} if found is not None else owners)
                ranked = _rank(list(candidates.values()), q)
                with _search_lock:
                    _search_cache.set(cache_key, ranked)
        finally:
            with _search_lock:
                _inflight.pop(cache_key, None)
    return ranked[:limit]
