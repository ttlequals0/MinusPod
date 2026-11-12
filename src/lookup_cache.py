"""Thread-safe TTL cache and the episode lookup cache that feed builders invalidate; imports nothing from main_app."""
from __future__ import annotations

import threading
import time


class TTLCache:
    """Simple thread-safe cache with time-to-live expiration."""

    def __init__(self, ttl_seconds: int = 30, max_size: int = 1024):
        self._cache = {}
        self._lock = threading.Lock()
        self._ttl = ttl_seconds
        self._max_size = max_size

    def get(self, key: str):
        """Get cached value if not expired, else return None."""
        with self._lock:
            if key in self._cache:
                value, expires = self._cache[key]
                if time.time() < expires:
                    return value
                del self._cache[key]
        return None

    def set(self, key: str, value):
        """Set cached value with TTL, evicting to stay under max_size."""
        with self._lock:
            now = time.time()
            if key not in self._cache and len(self._cache) >= self._max_size:
                self._evict_to_fit(now)
            self._cache[key] = (value, now + self._ttl)

    def _evict_to_fit(self, now):
        """Drop expired entries, then oldest ones, until under max_size."""
        expired = [k for k, (_, expires) in self._cache.items() if expires <= now]
        for k in expired:
            del self._cache[k]
        overage = len(self._cache) - self._max_size + 1
        if overage > 0:
            oldest = sorted(self._cache.items(), key=lambda kv: kv[1][1])[:overage]
            for k, _ in oldest:
                del self._cache[k]

    def invalidate(self, key: str = None):
        """Invalidate specific key or entire cache."""
        with self._lock:
            if key:
                self._cache.pop(key, None)
            else:
                self._cache.clear()

    def invalidate_prefix(self, prefix: str):
        """Invalidate every key starting with `prefix`.

        For caches whose keys namespace several entries under one owner, so
        the owner can drop all of its entries without tracking them.
        """
        with self._lock:
            for k in [k for k in self._cache if k.startswith(prefix)]:
                del self._cache[k]


# Upstream-RSS lookups behind serve_episode. A podcast client HEADs every
# unprocessed episode on each refresh, and each miss used to refetch and
# reparse the whole upstream feed. The TTL matches the background RSS
# refresh interval. feeds.py drops a feed's entries when it re-renders that
# feed, but the cache is per-process: under multiple gunicorn workers only
# the re-rendering worker sees the invalidation, so the other workers can
# serve entries up to one TTL stale. That bound is the guarantee, not the
# invalidation.
EPISODE_LOOKUP_TTL_SECONDS = 15 * 60

# One entry per episode per feed; a fetch caches the whole parsed feed, so
# the cap must hold a full library (feeds x feed length), not one page.
episode_lookup_cache = TTLCache(EPISODE_LOOKUP_TTL_SECONDS, max_size=8192)


def episode_lookup_key(slug: str, episode_id: str) -> str:
    """Cache key for one episode. Namespaced by slug so a feed can drop all
    of its own entries without touching anyone else's."""
    return f"{slug}\n{episode_id}"


def invalidate_episode_lookup_cache(slug: str) -> None:
    """Drop every cached lookup for one feed."""
    episode_lookup_cache.invalidate_prefix(f"{slug}\n")
