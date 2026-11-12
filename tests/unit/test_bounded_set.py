from utils.bounded_set import BoundedSet
from utils.ttl_cache import TTLCache


def test_add_reports_new_keys_and_evicts_the_oldest():
    s = BoundedSet(2)
    assert s.add('a') and s.add('b')
    assert not s.add('a')
    assert s.add('c')
    assert 'a' in s and 'b' not in s and len(s) == 2
    s.clear()
    assert len(s) == 0


def test_ttl_cache_uses_the_given_clock():
    now = [0.0]
    cache = TTLCache(10, 4, clock=lambda: now[0])
    cache.set('k', 1)
    now[0] = 9.9
    assert cache.get('k') == 1
    now[0] = 10.0
    assert cache.get('k') is None
