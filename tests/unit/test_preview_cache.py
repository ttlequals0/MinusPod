"""Preview slice cache: expiry sweep on read and insert, entry and byte caps."""
import pytest

from api import preview_cache


@pytest.fixture(autouse=True)
def cache(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(preview_cache, '_clock', lambda: now[0])
    monkeypatch.setattr(preview_cache, '_slice_cache', preview_cache.OrderedDict())
    return now


def test_a_read_sweeps_every_expired_entry(cache):
    preview_cache.remember_slice('a', b'1', 1)
    preview_cache.remember_slice('b', b'2', 2)
    cache[0] += preview_cache.SLICE_CACHE_TTL_S + 1
    assert preview_cache.cached_slice('missing') is None
    assert len(preview_cache._slice_cache) == 0


def test_an_unexpired_entry_is_still_served(cache):
    preview_cache.remember_slice('a', b'1', 1)
    cache[0] += preview_cache.SLICE_CACHE_TTL_S - 1
    assert preview_cache.cached_slice('a') == b'1'


def test_an_insert_sweeps_expired_entries(cache):
    preview_cache.remember_slice('a', b'1', 1)
    cache[0] += preview_cache.SLICE_CACHE_TTL_S + 1
    preview_cache.remember_slice('b', b'2', 2)
    assert list(preview_cache._slice_cache) == ['b']


def test_entry_count_is_capped(cache):
    for i in range(preview_cache.SLICE_CACHE_ENTRIES + 2):
        preview_cache.remember_slice(i, b'x', 1)
    assert list(preview_cache._slice_cache) == [2, 3, 4, 5]


def test_oldest_entries_are_evicted_until_under_the_byte_cap(cache, monkeypatch):
    monkeypatch.setattr(preview_cache, 'SLICE_CACHE_MAX_BYTES', 10)
    preview_cache.remember_slice('a', b'1234', 1)
    preview_cache.remember_slice('b', b'1234', 1)
    preview_cache.remember_slice('c', b'123456', 1)
    assert list(preview_cache._slice_cache) == ['b', 'c']
    preview_cache.remember_slice('d', b'1234', 1)
    assert list(preview_cache._slice_cache) == ['c', 'd']


def test_a_read_refreshes_recency_for_byte_eviction(cache, monkeypatch):
    monkeypatch.setattr(preview_cache, 'SLICE_CACHE_MAX_BYTES', 10)
    preview_cache.remember_slice('a', b'1234', 1)
    preview_cache.remember_slice('b', b'1234', 1)
    preview_cache.cached_slice('a')
    preview_cache.remember_slice('c', b'1234', 1)
    assert list(preview_cache._slice_cache) == ['a', 'c']


def test_an_oversized_entry_is_not_cached_and_evicts_nothing(cache, monkeypatch):
    monkeypatch.setattr(preview_cache, 'SLICE_CACHE_MAX_BYTES', 10)
    preview_cache.remember_slice('a', b'1234', 1)
    preview_cache.remember_slice('big', b'x' * 11, 1)
    assert list(preview_cache._slice_cache) == ['a']
    assert preview_cache.cached_slice('big') is None


def test_the_cap_is_64_mib():
    assert preview_cache.SLICE_CACHE_MAX_BYTES == 64 * 1024 * 1024
