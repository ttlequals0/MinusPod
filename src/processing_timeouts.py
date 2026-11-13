"""Processing timeout configuration.

Two in-flight thresholds:
- soft (default 3600s): status_service clears the current_job and stale queue entries.
- hard (default 7200s): processing_queue force-releases the lock, even if held
  by a stuck thread of this process.

Precedence: DB setting > env var > default. DB values apply without a restart;
a short TTL cache keeps hot paths off the DB.
"""
from __future__ import annotations

import logging
import os
import threading
import time

logger = logging.getLogger('podcast.processing_timeouts')

DEFAULT_SOFT_SECONDS = 3600
DEFAULT_HARD_SECONDS = 7200

_SOFT_KEY = 'processing_soft_timeout_seconds'
_HARD_KEY = 'processing_hard_timeout_seconds'
_SOFT_ENV = 'PROCESSING_SOFT_TIMEOUT'
_HARD_ENV = 'PROCESSING_HARD_TIMEOUT'

_CACHE_TTL_SECONDS = 5.0

SOFT_MIN = 300       # 5 min floor
HARD_MAX = 86400     # 24 h ceiling

_cache_lock = threading.Lock()
_cache: dict[str, tuple[float, int]] = {}


def _resolve(key: str, env_name: str, default: int) -> int:
    now = time.time()
    with _cache_lock:
        cached = _cache.get(key)
        if cached and now - cached[0] < _CACHE_TTL_SECONDS:
            return cached[1]

    value: int | None = None
    try:
        # Import from `database`, not `api.get_database`: early-startup callers
        # run before `api` finishes importing (circular ImportError, silent default).
        from database import Database
        raw = Database().get_setting(key)
        if raw is not None and str(raw).strip():
            try:
                value = int(raw)
            except ValueError:
                logger.warning(f"Ignoring non-integer DB setting {key}={raw!r}")
    except Exception as e:
        logger.debug(f"Could not read {key} from DB: {e}")

    if value is None:
        raw_env = os.environ.get(env_name)
        if raw_env:
            try:
                value = int(raw_env)
            except ValueError:
                logger.warning(f"Ignoring non-integer {env_name}={raw_env!r}")

    if value is None:
        value = default

    with _cache_lock:
        _cache[key] = (now, value)
    return value


def get_soft_timeout() -> int:
    return _resolve(_SOFT_KEY, _SOFT_ENV, DEFAULT_SOFT_SECONDS)


def get_hard_timeout() -> int:
    return _resolve(_HARD_KEY, _HARD_ENV, DEFAULT_HARD_SECONDS)


def invalidate_cache() -> None:
    with _cache_lock:
        _cache.clear()


def validate(soft: int, hard: int) -> str | None:
    """Return an error string if invalid, else None."""
    # JSON booleans pass isinstance(int); reject them explicitly.
    if type(soft) is not int or type(hard) is not int:
        return 'timeouts must be integers'
    if soft < SOFT_MIN:
        return f'soft timeout must be >= {SOFT_MIN} seconds'
    if hard <= soft:
        return 'hard timeout must be greater than soft timeout'
    if hard > HARD_MAX:
        return f'hard timeout must be <= {HARD_MAX} seconds'
    return None
