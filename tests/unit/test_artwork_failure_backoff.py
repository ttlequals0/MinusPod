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
                      return_value=False) as fetch:
        assert storage.download_artwork('example-feed', URL) is False
        assert storage.download_artwork('example-feed', URL) is False
        assert storage.download_artwork('example-feed', URL) is False
    # Refresh runs every few minutes; one attempt should cover all of them.
    assert fetch.call_count == 1


def test_force_retries_even_after_a_failure(storage):
    # The manual "refresh artwork" action must not be swallowed by the memo.
    with patch.object(storage, '_download_artwork_uncached',
                      return_value=False) as fetch:
        storage.download_artwork('example-feed', URL)
        storage.download_artwork('example-feed', URL, force=True)
    assert fetch.call_count == 2


def test_a_changed_url_retries_immediately(storage):
    with patch.object(storage, '_download_artwork_uncached',
                      return_value=False) as fetch:
        storage.download_artwork('example-feed', URL)
        storage.download_artwork('example-feed', 'http://example.com/new.jpg')
    assert fetch.call_count == 2


def test_a_different_feed_is_unaffected(storage):
    with patch.object(storage, '_download_artwork_uncached',
                      return_value=False) as fetch:
        storage.download_artwork('feed-one', URL)
        storage.download_artwork('feed-two', URL)
    assert fetch.call_count == 2


def test_success_leaves_no_block_behind(storage):
    with patch.object(storage, '_download_artwork_uncached',
                      return_value=True) as fetch:
        assert storage.download_artwork('example-feed', URL) is True
        assert storage.download_artwork('example-feed', URL) is True
    assert fetch.call_count == 2


def test_recovery_once_the_entry_expires(storage):
    storage._artwork_failure_cache._ttl = 0
    with patch.object(storage, '_download_artwork_uncached',
                      return_value=False) as fetch:
        storage.download_artwork('example-feed', URL)
        storage.download_artwork('example-feed', URL)
    assert fetch.call_count == 2


def test_empty_url_never_reaches_the_fetch(storage):
    with patch.object(storage, '_download_artwork_uncached') as fetch:
        assert storage.download_artwork('example-feed', '') is False
    fetch.assert_not_called()
