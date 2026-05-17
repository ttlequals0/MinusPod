"""Minimal TTL cache helper.

Used by callers that previously rolled their own time-based cache. Single
sentinel (`_MISSING`) distinguishes "no entry" from "entry stored as None".

Time source: `time.monotonic()`. No threading lock -- callers are
single-threaded per request worker, or wrap usage in their own lock when
shared across threads.
"""
import time
from typing import Any


_MISSING = object()


class TTLCache:
    """Dict-like cache where entries expire after `ttl_seconds`."""

    def __init__(self, ttl_seconds: float):
        self._ttl = float(ttl_seconds)
        self._store: dict = {}

    def get(self, key, default=None):
        """Return cached value if fresh, else `default`. Lazy eviction."""
        entry = self._store.get(key, _MISSING)
        if entry is _MISSING:
            return default
        value, ts = entry
        if (time.monotonic() - ts) >= self._ttl:
            # Expired -- evict and return default
            self._store.pop(key, None)
            return default
        return value

    def set(self, key, value: Any) -> None:
        """Store value with current monotonic timestamp."""
        self._store[key] = (value, time.monotonic())

    def clear(self) -> None:
        """Drop all cached entries."""
        self._store.clear()
