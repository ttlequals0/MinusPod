"""Shared mutable state for main_app sub-modules.

Provides module-level containers that multiple main_app modules need to share
(e.g. log-dedup sets that were a single object in the original monolith). The
episode lookup cache lives in lookup_cache so feed builders can reach it
without importing main_app.
"""
from __future__ import annotations

from lookup_cache import (  # noqa: F401
    EPISODE_LOOKUP_TTL_SECONDS, episode_lookup_cache, episode_lookup_key, invalidate_episode_lookup_cache,
)
from utils.bounded_set import BoundedSet

# Track episodes already warned about permanent failure (avoid log spam on
# repeated requests). Shared across routes.py and processing.py so the dedup
# works across both code paths. Bounded so a worker that serves a very large
# library cannot grow the set unboundedly.
permanently_failed_warned = BoundedSet(maxsize=10_000)
