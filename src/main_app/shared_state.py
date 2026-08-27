"""Shared mutable state for main_app sub-modules.

Provides module-level containers that multiple main_app modules need to share
(e.g. log-dedup sets that were a single object in the original monolith).
"""
from __future__ import annotations

import threading
from collections import OrderedDict
from collections.abc import Hashable

from main_app.cache import TTLCache

# Upstream-RSS lookups behind serve_episode. A podcast client HEADs every
# unprocessed episode on each refresh, and each miss used to refetch and
# reparse the whole upstream feed. The TTL matches the background RSS
# refresh interval, and feeds.py drops a feed's entries whenever it
# re-renders that feed, so a stale entry cannot outlive a real change.
EPISODE_LOOKUP_TTL_SECONDS = 15 * 60

episode_lookup_cache = TTLCache(EPISODE_LOOKUP_TTL_SECONDS)


def episode_lookup_key(slug: str, episode_id: str) -> str:
    """Cache key for one episode. Namespaced by slug so a feed can drop all
    of its own entries without touching anyone else's."""
    return f"{slug}\n{episode_id}"


def invalidate_episode_lookup_cache(slug: str) -> None:
    """Drop every cached lookup for one feed."""
    episode_lookup_cache.invalidate_prefix(f"{slug}\n")


class _BoundedSet:
    """Thread-safe set with a fixed upper bound. Evicts the oldest entry
    when the capacity would be exceeded.

    Used for log-dedup sets so a long-running worker cannot grow the set
    unboundedly (one entry per distinct episode permanently-failed).
    """

    def __init__(self, maxsize: int) -> None:
        self._maxsize = maxsize
        self._order: OrderedDict[Hashable, None] = OrderedDict()
        self._lock = threading.Lock()

    def __contains__(self, key: Hashable) -> bool:
        with self._lock:
            return key in self._order

    def add(self, key: Hashable) -> None:
        with self._lock:
            if key in self._order:
                self._order.move_to_end(key)
                return
            self._order[key] = None
            while len(self._order) > self._maxsize:
                self._order.popitem(last=False)

    def __len__(self) -> int:
        with self._lock:
            return len(self._order)


# Track episodes already warned about permanent failure (avoid log spam on
# repeated requests). Shared across routes.py and processing.py so the dedup
# works across both code paths. Bounded so a worker that serves a very large
# library cannot grow the set unboundedly.
permanently_failed_warned = _BoundedSet(maxsize=10_000)
