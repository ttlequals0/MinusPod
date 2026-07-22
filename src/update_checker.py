"""Check GitHub Releases for newer MinusPod versions.

Channels: 'stable' (newest non-prerelease) and 'edge' (newest release of
any kind). Drafts are ignored. This module owns the release fetch, an
in-process cache, version comparison, the /system/updates payload, and
the daily background tick that notifies once per newly seen version.
"""
import json
import logging
import threading
import time
from datetime import datetime, timezone

from config import HTTP_TIMEOUT_API
from utils.community_tags import GITHUB_REPO
from utils.safe_http import URLTrust, get_capped
from version import __version__

logger = logging.getLogger('update_checker')

NOTES_MAX_CHARS = 2000


def parse_version(text):
    """'v2.73.0' or '2.73.0' -> (2, 73, 0); None when not three ints."""
    if not text:
        return None
    parts = str(text).strip().lstrip('v').split('.')
    if len(parts) != 3:
        return None
    try:
        return tuple(int(p) for p in parts)
    except ValueError:
        return None


def _release_info(release):
    date = (release.get('published_at') or '')[:10] or None
    return {
        'version': (release.get('tag_name') or '').lstrip('v'),
        'releaseDate': date,
        'url': release.get('html_url'),
        'notes': (release.get('body') or '')[:NOTES_MAX_CHARS],
    }


def build_status(releases, channel, current_version=__version__):
    """Assemble the /system/updates payload from a GitHub releases list.

    The releases list is newest-first, as GitHub returns it.
    """
    published = [r for r in releases if not r.get('draft')]
    stable = next((r for r in published if not r.get('prerelease')), None)
    edge = published[0] if published else None

    current = {'version': current_version}
    for r in published:
        if (r.get('tag_name') or '').lstrip('v') == current_version:
            date = (r.get('published_at') or '')[:10]
            if date:
                current['releaseDate'] = date
            break

    status = {
        'current': current,
        'stable': _release_info(stable) if stable else None,
        'edge': _release_info(edge) if edge else None,
        'channel': channel,
        'updateAvailable': False,
    }
    target = status['stable'] if channel == 'stable' else status['edge']
    cur = parse_version(current_version)
    if target and cur:
        remote = parse_version(target['version'])
        if remote and remote > cur:
            status['updateAvailable'] = True
    return status


RELEASES_URL = f'https://api.github.com/repos/{GITHUB_REPO}/releases?per_page=100'
RELEASES_MAX_BYTES = 2 * 1024 * 1024
CACHE_TTL_S = 6 * 3600
REFRESH_MIN_INTERVAL_S = 30

_cache_lock = threading.Lock()
_fetch_lock = threading.Lock()
_cache = {'at': 0.0, 'releases': None}


def fetch_releases():
    """Fetch the releases list from the GitHub API (newest first)."""
    body = get_capped(
        RELEASES_URL, URLTrust.OPERATOR_CONFIGURED, RELEASES_MAX_BYTES,
        timeout=HTTP_TIMEOUT_API,
        headers={'Accept': 'application/vnd.github+json',
                 'User-Agent': f'MinusPod/{__version__}'})
    releases = json.loads(body.decode('utf-8'))
    if not isinstance(releases, list):
        raise ValueError('unexpected GitHub releases payload')
    return releases


def get_releases(force=False):
    """Cached releases list. force bypasses the TTL but a live fetch is
    still throttled to once per REFRESH_MIN_INTERVAL_S."""

    def _cached(force_inner):
        with _cache_lock:
            have = _cache['releases'] is not None
            age = time.time() - _cache['at']
            if have and (age < REFRESH_MIN_INTERVAL_S
                         or (not force_inner and age < CACHE_TTL_S)):
                return _cache['releases']
        return None

    cached = _cached(force)
    if cached is not None:
        return cached
    with _fetch_lock:
        # another thread may have fetched while we waited on the lock
        cached = _cached(force)
        if cached is not None:
            return cached
        releases = fetch_releases()
        with _cache_lock:
            _cache['releases'] = releases
            _cache['at'] = time.time()
        return releases


def get_update_status(db, force=False):
    """The /system/updates payload for this instance's channel setting."""
    channel = db.get_setting('update_channel') or 'stable'
    if channel not in ('stable', 'edge'):
        channel = 'stable'
    return build_status(get_releases(force=force), channel)


CHECK_INTERVAL_S = 24 * 3600


def _parse_iso(text):
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def update_check_tick(db):
    """Daily update check. Fires the Update Available notification once
    per newly seen version on the selected channel. Never raises."""
    if not db.get_setting_bool('update_check_enabled', default=True):
        return
    last_run = _parse_iso(db.get_setting('update_check_last_run'))
    now = datetime.now(timezone.utc)
    if last_run is not None and (now - last_run).total_seconds() < CHECK_INTERVAL_S:
        return
    try:
        status = get_update_status(db)
    except Exception as e:
        logger.warning(f"Update check failed: {e}")
        return
    db.set_setting('update_check_last_run', now.isoformat())
    if not status['updateAvailable']:
        return
    target = status['stable'] if status['channel'] == 'stable' else status['edge']
    remote = parse_version(target['version'])
    last_notified = parse_version(db.get_setting('update_check_last_notified'))
    if last_notified and remote and remote <= last_notified:
        return
    import webhook_service
    webhook_service.fire_update_available_event(
        target['version'], status['channel'],
        target.get('releaseDate'), target.get('url'))
    db.set_setting('update_check_last_notified', target['version'])
