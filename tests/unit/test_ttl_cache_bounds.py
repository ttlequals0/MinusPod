"""Tests for size-bounded eviction in both TTLCache implementations (#621)."""
from tests.app_bootstrap import bootstrap
bootstrap('ttl_bounds_test_')

import threading
import time

from main_app.cache import TTLCache as MainAppTTLCache
from utils.ttl_cache import TTLCache as UtilsTTLCache


# --- main_app.cache.TTLCache ---

def test_main_app_cap_enforced():
    cache = MainAppTTLCache(ttl_seconds=60, max_size=5)
    for i in range(6):
        cache.set(f'k{i}', i)
    assert len(cache._cache) == 5
    assert cache.get('k5') == 5


def test_main_app_expired_evicted_before_live():
    cache = MainAppTTLCache(ttl_seconds=0.01, max_size=2)
    cache.set('old1', 'stale1')
    cache.set('old2', 'stale2')
    time.sleep(0.02)
    cache.set('new', 'fresh')
    assert len(cache._cache) == 1
    assert cache.get('new') == 'fresh'
    assert cache.get('old1') is None
    assert cache.get('old2') is None


def test_main_app_oldest_live_evicted_when_none_expired():
    cache = MainAppTTLCache(ttl_seconds=60, max_size=2)
    cache.set('a', 1)
    cache.set('b', 2)
    cache.set('c', 3)
    assert len(cache._cache) == 2
    assert cache.get('a') is None
    assert cache.get('b') == 2
    assert cache.get('c') == 3


def test_main_app_overwrite_at_cap_does_not_evict():
    cache = MainAppTTLCache(ttl_seconds=60, max_size=2)
    cache.set('a', 1)
    cache.set('b', 2)
    cache.set('b', 22)
    assert len(cache._cache) == 2
    assert cache.get('a') == 1
    assert cache.get('b') == 22


def test_main_app_default_construction_unchanged():
    cache = MainAppTTLCache()
    cache.set('k', 'v')
    assert cache.get('k') == 'v'


# --- utils.ttl_cache.TTLCache ---

def test_utils_cap_enforced():
    cache = UtilsTTLCache(ttl_seconds=60, max_size=5)
    for i in range(6):
        cache.set(f'k{i}', i)
    assert len(cache._store) == 5
    assert cache.get('k5') == 5


def test_utils_expired_evicted_before_live(monkeypatch):
    cache = UtilsTTLCache(ttl_seconds=10, max_size=2)
    now = [1000.0]
    monkeypatch.setattr(time, 'monotonic', lambda: now[0])
    cache.set('old1', 'stale1')
    cache.set('old2', 'stale2')
    now[0] += 20
    cache.set('new', 'fresh')
    assert len(cache._store) == 1
    assert cache.get('new') == 'fresh'
    assert cache.get('old1') is None
    assert cache.get('old2') is None


def test_utils_oldest_live_evicted_when_none_expired():
    cache = UtilsTTLCache(ttl_seconds=60, max_size=2)
    cache.set('a', 1)
    cache.set('b', 2)
    cache.set('c', 3)
    assert len(cache._store) == 2
    assert cache.get('a') is None
    assert cache.get('b') == 2
    assert cache.get('c') == 3


def test_utils_overwrite_at_cap_does_not_evict():
    cache = UtilsTTLCache(ttl_seconds=60, max_size=2)
    cache.set('a', 1)
    cache.set('b', 2)
    cache.set('b', 22)
    assert len(cache._store) == 2
    assert cache.get('a') == 1
    assert cache.get('b') == 22


def test_utils_default_construction_unchanged():
    cache = UtilsTTLCache(ttl_seconds=60)
    cache.set('k', 'v')
    assert cache.get('k') == 'v'


class _MutatingStore(dict):
    """Runs hook once, in the middle of an items() walk."""
    hook = None

    def items(self):
        for position, pair in enumerate(super().items()):
            yield pair
            if position == 0 and self.hook:
                hook, self.hook = self.hook, None
                hook()


def test_utils_eviction_survives_a_concurrent_insert():
    cache = UtilsTTLCache(ttl_seconds=60, max_size=2)
    store = _MutatingStore()
    cache._store = store
    cache.set('a', 1)
    cache.set('b', 2)

    def insert_from_another_thread():
        worker = threading.Thread(target=cache.set, args=('other', 3))
        worker.start()
        # Without a lock the insert lands inside the walk; with one it waits for the walk to end.
        worker.join(timeout=0.2)
        workers.append(worker)

    workers = []
    store.hook = insert_from_another_thread
    cache.set('c', 4)
    workers[0].join(timeout=5)
    assert not workers[0].is_alive()
    assert len(cache._store) <= 2
