"""A broken artwork URL is not refetched on every feed refresh."""
import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from storage import Storage  # noqa: E402


@pytest.fixture
def storage(tmp_path):
    Storage._instance = None
    s = Storage(str(tmp_path))
    s._artwork_failure_cache.clear()
    yield s
    Storage._instance = None


URL = 'http://example.com/artwork.jpg'


def test_failed_url_is_not_retried_on_the_next_refresh(storage):
    with patch.object(storage, '_download_artwork_uncached',
                      return_value=(False, 'error')) as fetch:
        assert storage.download_artwork('example-feed', URL) is False
        assert storage.download_artwork('example-feed', URL) is False
        assert storage.download_artwork('example-feed', URL) is False
    # Refresh runs every few minutes; one attempt should cover all of them.
    assert fetch.call_count == 1


def test_force_retries_even_after_a_failure(storage):
    # The manual "refresh artwork" action must not be swallowed by the memo.
    with patch.object(storage, '_download_artwork_uncached',
                      return_value=(False, 'error')) as fetch:
        storage.download_artwork('example-feed', URL)
        storage.download_artwork('example-feed', URL, force=True)
    assert fetch.call_count == 2


def test_a_changed_url_retries_immediately(storage):
    with patch.object(storage, '_download_artwork_uncached',
                      return_value=(False, 'error')) as fetch:
        storage.download_artwork('example-feed', URL)
        storage.download_artwork('example-feed', 'http://example.com/new.jpg')
    assert fetch.call_count == 2


def test_a_different_feed_is_unaffected(storage):
    with patch.object(storage, '_download_artwork_uncached',
                      return_value=(False, 'error')) as fetch:
        storage.download_artwork('feed-one', URL)
        storage.download_artwork('feed-two', URL)
    assert fetch.call_count == 2


def test_success_leaves_no_block_behind(storage):
    with patch.object(storage, '_download_artwork_uncached',
                      return_value=(True, None)) as fetch:
        assert storage.download_artwork('example-feed', URL) is True
        assert storage.download_artwork('example-feed', URL) is True
    assert fetch.call_count == 2


def test_recovery_once_the_entry_expires(storage):
    storage._artwork_failure_cache._ttl = 0
    with patch.object(storage, '_download_artwork_uncached',
                      return_value=(False, 'error')) as fetch:
        storage.download_artwork('example-feed', URL)
        storage.download_artwork('example-feed', URL)
    assert fetch.call_count == 2


def test_empty_url_never_reaches_the_fetch(storage):
    with patch.object(storage, '_download_artwork_uncached') as fetch:
        assert storage.download_artwork('example-feed', '') is False
    fetch.assert_not_called()


def test_success_does_not_occupy_a_cache_slot(storage):
    # The cache exists to remember failures. Storing successes too fills it
    # with entries nothing ever reads, which then evict the real ones.
    with patch.object(storage, '_download_artwork_uncached', return_value=(True, None)):
        storage.download_artwork('example-feed', URL)
    assert storage._artwork_failure_cache._store == {}


def test_successes_do_not_evict_a_live_failure_entry(storage):
    storage._artwork_failure_cache._max_size = 4

    with patch.object(storage, '_download_artwork_uncached',
                      return_value=(False, 'error')) as fetch:
        storage.download_artwork('broken-feed', URL)
    assert fetch.call_count == 1

    # A busy install downloads far more covers than it fails to download.
    with patch.object(storage, '_download_artwork_uncached', return_value=(True, None)):
        for i in range(20):
            storage.download_artwork(f'healthy-feed-{i}', f'{URL}?{i}')

    with patch.object(storage, '_download_artwork_uncached',
                      return_value=(False, 'error')) as refetch:
        storage.download_artwork('broken-feed', URL)
    refetch.assert_not_called()


def test_episode_successes_do_not_evict_a_feed_failure_entry(storage):
    # Both helpers share one cache, and episode covers vastly outnumber feed
    # covers, so episode traffic is what actually pushes feed entries out.
    storage._artwork_failure_cache._max_size = 4

    with patch.object(storage, '_download_artwork_uncached',
                      return_value=(False, 'error')):
        storage.download_artwork('broken-feed', URL)

    with patch.object(storage, '_download_episode_artwork_uncached',
                      return_value=True):
        for i in range(20):
            storage.download_episode_artwork(
                'healthy-feed', f'{i:012x}', f'{URL}?{i}')

    with patch.object(storage, '_download_artwork_uncached',
                      return_value=(False, 'error')) as refetch:
        storage.download_artwork('broken-feed', URL)
    refetch.assert_not_called()


def test_a_forced_retry_that_succeeds_unblocks_the_normal_path(storage):
    with patch.object(storage, '_download_artwork_uncached',
                      return_value=(False, 'error')):
        storage.download_artwork('example-feed', URL)

    with patch.object(storage, '_download_artwork_uncached',
                      return_value=(True, None)):
        assert storage.download_artwork('example-feed', URL, force=True) is True

    with patch.object(storage, '_download_artwork_uncached',
                      return_value=(True, None)) as fetch:
        assert storage.download_artwork('example-feed', URL) is True
    fetch.assert_called_once()


@pytest.mark.parametrize('status', ['error', 'not_found'])
def test_feed_ua_change_retries_failure_without_affecting_other_feeds(storage, status):
    for slug in ('feed-one', 'feed-two'):
        storage.db.create_podcast(slug, 'https://example.com/feed.xml')
    with patch.object(storage, '_download_artwork_uncached', return_value=(False, status)) as fetch:
        for slug in ('feed-one', 'feed-two'):
            storage.download_artwork(slug, URL)
        storage.db.update_podcast('feed-one', download_user_agent_override='Feed/2.0')
        storage.download_artwork('feed-one', URL)
        storage.download_artwork('feed-two', URL)
        storage.download_artwork('feed-one', URL)
    assert fetch.call_count == 3
    storage._artwork_failure_cache.clear()
    storage._artwork_404_cache.clear()
    with patch.object(storage, '_download_artwork_uncached') as fetch:
        storage.download_artwork('feed-one', URL)
    fetch.assert_not_called()
    storage.db.delete_podcast('feed-one')
    storage.db.delete_podcast('feed-two')


def test_global_ua_change_retries_feed_and_episode_failures(storage, monkeypatch):
    monkeypatch.setattr('storage.download_user_agent', lambda podcast=None: 'First/1.0')
    with patch.object(storage, '_download_artwork_uncached', return_value=(False, 'error')) as feed_fetch, \
         patch.object(storage, '_download_episode_artwork_uncached', return_value=False) as episode_fetch:
        storage.download_artwork('example-feed', URL)
        storage.download_episode_artwork('example-feed', 'a1b2c3d4e5f6', URL)
        monkeypatch.setattr('storage.download_user_agent', lambda podcast=None: 'Second/2.0')
        storage.download_artwork('example-feed', URL)
        storage.download_episode_artwork('example-feed', 'a1b2c3d4e5f6', URL)
        storage.download_artwork('example-feed', URL)
        storage.download_episode_artwork('example-feed', 'a1b2c3d4e5f6', URL)
    assert feed_fetch.call_count == 2
    assert episode_fetch.call_count == 2
