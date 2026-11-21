"""Repeated episode lookups must not refetch the upstream RSS feed (clients send a HEAD per
unprocessed episode every refresh cycle)."""
from unittest.mock import MagicMock, patch

import pytest

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('episode_lookup_cache_test_', reset_storage=True)

from main_app import routes  # noqa: E402
from main_app.shared_state import (  # noqa: E402
    LOOKUP_MISS_THROTTLE_SECONDS,
    episode_lookup_cache,
    lookup_miss_cache,
    invalidate_episode_lookup_cache,
)

SLUG = 'example-feed'
EPISODE_ID = 'abc123def456'
FEED_MAP = {SLUG: {'in': 'https://example.com/feed.xml'}}


@pytest.fixture(autouse=True)
def clear_cache():
    episode_lookup_cache.invalidate()
    lookup_miss_cache.invalidate()
    yield
    episode_lookup_cache.invalidate()
    lookup_miss_cache.invalidate()


def _upstream_episode():
    return {
        'id': EPISODE_ID,
        'url': 'https://example.com/ep.mp3',
        'title': 'Episode One',
        'description': 'desc',
        'artwork_url': None,
        'published': '2026-01-01',
    }


def _patch_upstream(episodes=None):
    """Patch the fetch/parse/extract trio _lookup_episode walks."""
    parsed = MagicMock()
    parsed.feed = {'title': 'Example Show'}
    return (
        patch.object(routes.rss_parser, 'fetch_feed', return_value='<rss/>'),
        patch.object(routes.rss_parser, 'parse_feed', return_value=parsed),
        patch.object(routes.rss_parser, 'extract_episodes',
                     return_value=episodes if episodes is not None
                     else [_upstream_episode()]),
    )


def test_repeat_lookup_does_not_refetch_the_feed():
    fetch, parse, extract = _patch_upstream()
    with fetch as fetch_mock, parse, extract:
        first = routes._lookup_episode(SLUG, EPISODE_ID, FEED_MAP)
        second = routes._lookup_episode(SLUG, EPISODE_ID, FEED_MAP)
    assert first == second
    assert first[1] == 'Example Show'
    fetch_mock.assert_called_once()


def test_rebuilding_the_served_feed_invalidates_the_entry():
    fetch, parse, extract = _patch_upstream()
    with fetch as fetch_mock, parse, extract:
        routes._lookup_episode(SLUG, EPISODE_ID, FEED_MAP)
        invalidate_episode_lookup_cache(SLUG)
        routes._lookup_episode(SLUG, EPISODE_ID, FEED_MAP)
    assert fetch_mock.call_count == 2


def test_invalidating_one_feed_leaves_other_feeds_cached():
    other_map = dict(FEED_MAP)
    other_map['other-feed'] = {'in': 'https://example.com/other.xml'}
    fetch, parse, extract = _patch_upstream()
    with fetch as fetch_mock, parse, extract:
        routes._lookup_episode(SLUG, EPISODE_ID, other_map)
        routes._lookup_episode('other-feed', EPISODE_ID, other_map)
        assert fetch_mock.call_count == 2

        invalidate_episode_lookup_cache(SLUG)
        routes._lookup_episode('other-feed', EPISODE_ID, other_map)
        assert fetch_mock.call_count == 2

        routes._lookup_episode(SLUG, EPISODE_ID, other_map)
        assert fetch_mock.call_count == 3


def test_the_database_fallback_is_cached_too():
    # An episode that aged off the upstream feed hits the DB on every
    # request otherwise, which is the same repeated work one layer down.
    fetch, parse, extract = _patch_upstream(episodes=[])
    row = {
        'original_url': 'https://example.com/old.mp3',
        'title': 'Old Episode',
        'description': None,
        'artwork_url': None,
        'published_at': '2025-01-01',
        'podcast_title': 'Example Show',
    }
    with fetch, parse, extract, \
            patch.object(routes.db, 'get_episode', return_value=row) as get_ep:
        first = routes._lookup_episode(SLUG, EPISODE_ID, FEED_MAP)
        second = routes._lookup_episode(SLUG, EPISODE_ID, FEED_MAP)
    assert first[0]['url'] == 'https://example.com/old.mp3'
    assert first == second
    get_ep.assert_called_once()


def test_a_miss_is_not_cached_but_refetches_only_after_the_throttle():
    # The miss itself is never cached (a feed mid-publish must not 404 for the
    # whole TTL), but unknown ids cannot force a refetch inside the throttle.
    fetch, parse, extract = _patch_upstream(episodes=[])
    clock = [1000.0]
    with fetch as fetch_mock, parse, extract, \
            patch.object(routes.db, 'get_episode', return_value=None), \
            patch('lookup_cache.time.time', side_effect=lambda: clock[0]):
        assert routes._lookup_episode(SLUG, EPISODE_ID, FEED_MAP) == (None, None)
        assert fetch_mock.call_count == 1
        clock[0] += LOOKUP_MISS_THROTTLE_SECONDS + 1
        assert routes._lookup_episode(SLUG, EPISODE_ID, FEED_MAP) == (None, None)
    assert fetch_mock.call_count == 2


def test_unknown_ids_under_one_slug_fetch_upstream_once():
    fetch, parse, extract = _patch_upstream()
    clock = [1000.0]
    with fetch as fetch_mock, parse, extract, \
            patch.object(routes.db, 'get_episode', return_value=None), \
            patch('lookup_cache.time.time', side_effect=lambda: clock[0]):
        for i in range(5):
            assert routes._lookup_episode(SLUG, f'unknown{i:06d}', FEED_MAP) == (None, None)
        assert fetch_mock.call_count == 1

        # The throttle is per slug.
        other_map = {'other-feed': {'in': 'https://example.com/other.xml'}}
        routes._lookup_episode('other-feed', 'unknown999999', other_map)
        assert fetch_mock.call_count == 2

        # An id the feed does list is still found while throttled (cached by the first fetch).
        assert routes._lookup_episode(SLUG, EPISODE_ID, FEED_MAP)[0]['id'] == EPISODE_ID
        assert fetch_mock.call_count == 2


def test_first_lookup_of_a_listed_id_is_found_and_rows_bypass_the_throttle():
    fetch, parse, extract = _patch_upstream()
    row = {'original_url': 'https://example.com/db.mp3', 'podcast_title': 'Example Show'}
    with fetch as fetch_mock, parse, extract, \
            patch.object(routes.db, 'get_episode', return_value=None):
        assert routes._lookup_episode(SLUG, EPISODE_ID, FEED_MAP)[0]['id'] == EPISODE_ID
        routes._lookup_episode(SLUG, 'unknown000001', FEED_MAP)
        count = fetch_mock.call_count
    with fetch as fetch_mock, parse, extract, \
            patch.object(routes.db, 'get_episode', return_value=row):
        episode, _ = routes._lookup_episode(SLUG, 'dbrow0000001', FEED_MAP)
    assert episode['url'] == 'https://example.com/db.mp3'
    assert count == 2
    assert fetch_mock.call_count == 1


def test_listed_id_without_a_row_is_found_inside_the_miss_window():
    other = dict(_upstream_episode(), id='b2c3d4e5f6a1', url='https://example.com/b.mp3')
    fetch, parse, extract = _patch_upstream(episodes=[other])
    clock = [1000.0]
    with fetch as fetch_mock, parse, extract, \
            patch.object(routes.db, 'get_episode', return_value=None), \
            patch('lookup_cache.time.time', side_effect=lambda: clock[0]):
        assert routes._lookup_episode(SLUG, 'unknown000001', FEED_MAP) == (None, None)
        assert fetch_mock.call_count == 1
        episode_lookup_cache.invalidate()
        found, _ = routes._lookup_episode(SLUG, 'b2c3d4e5f6a1', FEED_MAP)
        assert found['url'] == 'https://example.com/b.mp3'
        assert fetch_mock.call_count == 2
        assert routes._lookup_episode(SLUG, 'unknown000002', FEED_MAP) == (None, None)
        assert fetch_mock.call_count == 2
