"""Outbound User-Agent resolution.

A leaf module on purpose: rss_parser and upstream_chapters hold no database
handle, and config.py cannot import Database without a cycle.

Two strings because hosts disagree. Bot mitigation on some CDNs refuses
browser UAs below a rolling version floor, while some feed hosts serve only a
declared podcast client.
"""
import logging
import threading

from config import APP_USER_AGENT, BROWSER_USER_AGENT, validate_user_agent
from utils.ttl_cache import TTLCache

logger = logging.getLogger('podcast.audio')

DOWNLOAD_UA_SETTING = 'download_user_agent'
FEED_UA_SETTING = 'feed_user_agent'

# Long enough that a busy download loop is not querying per request, short
# enough that an operator editing Settings sees the change take hold.
_CACHE_TTL_SECONDS = 30

_cache = TTLCache(ttl_seconds=_CACHE_TTL_SECONDS)
_cache_lock = threading.Lock()


def _resolve(setting_key: str, fallback: str) -> str:
    """Stored UA for `setting_key`, else `fallback`. Never raises: an
    unreadable database must not stop an outbound request."""
    with _cache_lock:
        cached = _cache.get(setting_key)
    if cached is not None:
        return cached

    value = fallback
    try:
        from database import Database
        stored = Database().get_setting(setting_key)
        if stored and validate_user_agent(stored):
            value = stored.strip()
        elif stored:
            logger.warning(
                f"Ignoring invalid {setting_key}, using the default instead")
    except Exception as e:
        logger.debug(f"Could not read {setting_key}, using the default: {e}")

    with _cache_lock:
        _cache.set(setting_key, value)
    return value


def download_user_agent() -> str:
    """UA for audio, artwork, and chapter fetches."""
    return _resolve(DOWNLOAD_UA_SETTING, BROWSER_USER_AGENT)


def feed_user_agent() -> str:
    """UA for RSS and feed-validation requests."""
    return _resolve(FEED_UA_SETTING, APP_USER_AGENT)


def invalidate_cache() -> None:
    """Drop cached values so the next request re-reads the settings."""
    with _cache_lock:
        _cache.clear()
