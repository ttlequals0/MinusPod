"""Small in-memory cache of encoded preview slices, dropped per pattern."""
import threading
import time
from collections import OrderedDict

SLICE_CACHE_ENTRIES = 4
SLICE_CACHE_TTL_S = 120

_clock = time.monotonic
# key -> (expires_at, mp3 bytes, pattern id); oldest first
_slice_cache = OrderedDict()
_slice_cache_lock = threading.Lock()


def cached_slice(key):
    """The unexpired encoded slice for a key, or None."""
    with _slice_cache_lock:
        entry = _slice_cache.get(key)
        if entry is None:
            return None
        if entry[0] <= _clock():
            del _slice_cache[key]
            return None
        _slice_cache.move_to_end(key)
        return entry[1]


def remember_slice(key, data, pattern_id):
    with _slice_cache_lock:
        now = _clock()
        for stale in [k for k, entry in _slice_cache.items() if entry[0] <= now]:
            del _slice_cache[stale]
        _slice_cache[key] = (now + SLICE_CACHE_TTL_S, data, pattern_id)
        _slice_cache.move_to_end(key)
        while len(_slice_cache) > SLICE_CACHE_ENTRIES:
            _slice_cache.popitem(last=False)


def drop_cached_slices(pattern_ids):
    """Forget every cached preview slice of the given patterns (one id or an iterable)."""
    ids = {pattern_ids} if isinstance(pattern_ids, int) else set(pattern_ids)
    with _slice_cache_lock:
        for key in [k for k, entry in _slice_cache.items() if entry[2] in ids]:
            del _slice_cache[key]
