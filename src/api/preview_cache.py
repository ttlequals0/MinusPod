"""Small in-memory cache of encoded preview slices, dropped per pattern."""
import threading
import time
from collections import OrderedDict

SLICE_CACHE_ENTRIES = 4
SLICE_CACHE_MAX_BYTES = 64 * 1024 * 1024
SLICE_CACHE_TTL_S = 120

_clock = time.monotonic
# key -> (expires_at, mp3 bytes, pattern id); oldest first
_slice_cache = OrderedDict()
_slice_cache_lock = threading.Lock()


def _sweep_expired():
    """Drop every expired entry; the caller holds the lock."""
    now = _clock()
    for stale in [k for k, entry in _slice_cache.items() if entry[0] <= now]:
        del _slice_cache[stale]


def cached_slice(key):
    """The unexpired encoded slice for a key, or None."""
    with _slice_cache_lock:
        _sweep_expired()
        entry = _slice_cache.get(key)
        if entry is None:
            return None
        _slice_cache.move_to_end(key)
        return entry[1]


def remember_slice(key, data, pattern_id):
    """Cache a slice; one larger than the byte cap is served by the caller but never kept."""
    with _slice_cache_lock:
        _sweep_expired()
        if len(data) > SLICE_CACHE_MAX_BYTES:
            return
        _slice_cache[key] = (_clock() + SLICE_CACHE_TTL_S, data, pattern_id)
        _slice_cache.move_to_end(key)
        total = sum(len(entry[1]) for entry in _slice_cache.values())
        while len(_slice_cache) > SLICE_CACHE_ENTRIES or total > SLICE_CACHE_MAX_BYTES:
            _, evicted = _slice_cache.popitem(last=False)
            total -= len(evicted[1])


def drop_cached_slices(pattern_ids):
    """Forget every cached preview slice of the given patterns (one id or an iterable)."""
    ids = {pattern_ids} if isinstance(pattern_ids, int) else set(pattern_ids)
    with _slice_cache_lock:
        for key in [k for k, entry in _slice_cache.items() if entry[2] in ids]:
            del _slice_cache[key]
