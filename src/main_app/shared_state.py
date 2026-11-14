"""Shared mutable state for main_app sub-modules (e.g. log-dedup sets).

The episode lookup cache lives in lookup_cache so feed builders can reach it
without importing main_app.
"""
from __future__ import annotations

from lookup_cache import (  # noqa: F401
    EPISODE_LOOKUP_TTL_SECONDS, episode_lookup_cache, episode_lookup_key, invalidate_episode_lookup_cache,
)
from utils.bounded_set import BoundedSet

# Episodes already warned about permanent failure, shared by routes.py and
# processing.py. Bounded so a very large library cannot grow it without limit.
permanently_failed_warned = BoundedSet(maxsize=10_000)
