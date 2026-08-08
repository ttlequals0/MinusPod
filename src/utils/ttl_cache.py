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

    def __init__(self, ttl_seconds: float, max_size: int = 1024):
        self._ttl = float(ttl_seconds)
        self._store: dict = {}
        self._max_size = max_size

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
        """Store value with current monotonic timestamp, evicting to fit max_size."""
        now = time.monotonic()
        if key not in self._store and len(self._store) >= self._max_size:
            self._evict_to_fit(now)
        self._store[key] = (value, now)

    def _evict_to_fit(self, now) -> None:
        """Drop expired entries, then oldest ones, until under max_size."""
        expired = [k for k, (_, ts) in self._store.items() if (now - ts) >= self._ttl]
        for k in expired:
            del self._store[k]
        overage = len(self._store) - self._max_size + 1
        if overage > 0:
            oldest = sorted(self._store.items(), key=lambda kv: kv[1][1])[:overage]
            for k, _ in oldest:
                del self._store[k]

    def clear(self) -> None:
        """Drop all cached entries."""
        self._store.clear()
