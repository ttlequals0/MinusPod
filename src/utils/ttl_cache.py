"""Minimal TTL cache helper.

A sentinel (`_MISSING`) distinguishes "no entry" from "entry stored as None".
Time source is `time.monotonic()` unless a `clock` callable is given. Every operation
holds an internal lock, so one instance is safe to share across threads.
"""
import threading
import time
from typing import Any


_MISSING = object()


class TTLCache:
    """Dict-like cache where entries expire after `ttl_seconds`."""

    def __init__(self, ttl_seconds: float, max_size: int = 1024, clock=None):
        self._ttl = float(ttl_seconds)
        self._clock = clock
        self._store: dict = {}
        self._max_size = max_size
        self._lock = threading.RLock()

    def _now(self) -> float:
        return self._clock() if self._clock else time.monotonic()

    def get(self, key, default=None):
        """Return cached value if fresh, else `default`. Lazy eviction."""
        with self._lock:
            entry = self._store.get(key, _MISSING)
            if entry is _MISSING:
                return default
            value, ts = entry
            if (self._now() - ts) >= self._ttl:
                self._store.pop(key, None)
                return default
            return value

    def set(self, key, value: Any) -> None:
        """Store value with current monotonic timestamp, evicting to fit max_size."""
        with self._lock:
            now = self._now()
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

    def delete(self, key) -> None:
        """Drop one entry if present."""
        with self._lock:
            self._store.pop(key, None)

    def clear(self) -> None:
        """Drop all cached entries."""
        with self._lock:
            self._store.clear()
