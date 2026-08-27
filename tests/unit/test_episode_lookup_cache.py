"""Repeated episode lookups must not refetch the upstream RSS feed.

Podcast clients send a HEAD for every unprocessed episode on every refresh
cycle, and each one used to pull and parse the whole upstream feed.
"""
from unittest.mock import MagicMock, patch

import pytest

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('episode_lookup_cache_test_', reset_storage=True)

from main_app import routes  # noqa: E402
from main_app.shared_state import (  # noqa: E402
    episode_lookup_cache,
    invalidate_episode_lookup_cache,
)

SLUG = 'example-feed'
EPISODE_ID = 'abc123def456'
FEED_MAP = {SLUG: {'in': 'https://example.com/feed.xml'}}


@pytest.fixture(autouse=True)
def clear_cache():
    episode_lookup_cache.invalidate()
    yield
    episode_lookup_cache.invalidate()


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


def test_a_miss_is_not_cached():
    # Nothing found means nothing to remember, and caching it would pin a
    # 404 for the whole TTL while the feed is mid-publish.
    fetch, parse, extract = _patch_upstream(episodes=[])
    with fetch as fetch_mock, parse, extract, \
            patch.object(routes.db, 'get_episode', return_value=None):
        assert routes._lookup_episode(SLUG, EPISODE_ID, FEED_MAP) == (None, None)
        assert routes._lookup_episode(SLUG, EPISODE_ID, FEED_MAP) == (None, None)
    assert fetch_mock.call_count == 2
