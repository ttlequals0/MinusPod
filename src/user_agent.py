"""Outbound User-Agent resolution.

A leaf module on purpose, following the processing_timeouts pattern: the value
is read on every outbound request, so it needs a cache and an explicit
invalidation hook rather than a settings query per call. It imports nothing
from the repo but config, deliberately. `utils.ttl_cache` would look like the
right cache to reuse here, but `utils/__init__` eagerly imports `utils.audio`,
which imports storage, which imports this module, so reaching for it puts this
module back in a cycle whenever it is imported before storage.

Two strings because hosts disagree. Bot mitigation on some CDNs refuses
browser UAs below a rolling version floor, while some feed hosts serve only a
declared podcast client.
"""
import logging
import threading
import time

from config import resolve_env_backed_default, validate_user_agent

logger = logging.getLogger('podcast.audio')

DOWNLOAD_UA_SETTING = 'download_user_agent'
FEED_UA_SETTING = 'feed_user_agent'

# Long enough that a busy download loop is not querying per request, short
# enough that an operator editing Settings sees the change take hold.
_CACHE_TTL_SECONDS = 30

# setting key -> (value, monotonic timestamp). Two keys, one TTL.
_cache: dict[str, tuple[str, float]] = {}
_cache_lock = threading.Lock()


def _resolve(setting_key: str) -> str:
    """Stored UA for `setting_key`, else the env-backed default. Never raises:
    an unreadable database must not stop an outbound request."""
    now = time.monotonic()
    with _cache_lock:
        entry = _cache.get(setting_key)
        if entry is not None and (now - entry[1]) < _CACHE_TTL_SECONDS:
            return entry[0]

    value = resolve_env_backed_default(setting_key)
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
        _cache[setting_key] = (value, time.monotonic())
    return value


def download_user_agent() -> str:
    """UA for audio, artwork, and chapter fetches."""
    return _resolve(DOWNLOAD_UA_SETTING)


def feed_user_agent() -> str:
    """UA for RSS and feed-validation requests."""
    return _resolve(FEED_UA_SETTING)


def invalidate_cache() -> None:
    """Drop cached values so the next request re-reads the settings."""
    with _cache_lock:
        _cache.clear()
