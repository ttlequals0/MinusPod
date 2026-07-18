"""Tests for RSS feed 304 Not Modified handling in refresh_rss_feed."""
import unittest
from unittest.mock import MagicMock, patch

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('feed_304_test_')
from main_app.feeds import refresh_rss_feed
import main_app.feeds as _feeds_module


class TestFeed304Refresh(unittest.TestCase):
    """Verify that last_checked_at is updated when upstream returns 304."""

    def setUp(self):
        # refresh_rss_feed has a 30s per-slug coalesce. Reset it
        # between tests so consecutive calls with the same fixture
        # slug are not skipped.
        _feeds_module._refresh_coalesce.invalidate()

    @patch('main_app.feeds.pattern_service')
    @patch('main_app.feeds.status_service')
    @patch('main_app.feeds.storage')
    @patch('main_app.feeds.rss_parser')
    @patch('main_app.feeds.db')
    def test_304_with_episodes_updates_last_checked_at(
        self, db, rss_parser, storage, status_service, pattern_service
    ):
        """When upstream returns 304 and episodes exist with artwork cached,
        last_checked_at should be updated so the feed is not perpetually stale."""
        # Simulate existing podcast with etag
        db.get_podcast_by_slug.return_value = {
            'id': 1, 'feed_url': 'https://example.com/rss',
            'etag': '"abc123"', 'last_modified': None,
            'artwork_cached': True
        }
        # Episodes exist
        db.get_episodes.return_value = ([], 5)

        # Upstream returns 304 (feed_content=None, but etag present)
        rss_parser.fetch_feed_conditional.return_value = (None, '"abc123"', None)

        storage.load_data_json.return_value = {'feed_url': 'https://example.com/rss'}

        result = refresh_rss_feed('test-podcast', 'https://example.com/rss')

        self.assertTrue(result)
        # last_checked_at must have been updated
        db.update_podcast.assert_called_once()
        call_kwargs = db.update_podcast.call_args
        self.assertEqual(call_kwargs[0][0], 'test-podcast')
        self.assertIn('last_checked_at', call_kwargs[1])
        status_service.complete_feed_refresh.assert_called_once_with('test-podcast', 0)

    @patch('main_app.feeds.pattern_service')
    @patch('main_app.feeds.status_service')
    @patch('main_app.feeds.storage')
    @patch('main_app.feeds.rss_parser')
    @patch('main_app.feeds.db')
    def test_304_with_missing_artwork_falls_through(
        self, db, rss_parser, storage, status_service, pattern_service
    ):
        """When upstream returns 304 but artwork is not cached,
        a full fetch should be forced (no early return)."""
        db.get_podcast_by_slug.return_value = {
            'id': 1, 'feed_url': 'https://example.com/rss',
            'etag': '"abc123"', 'last_modified': None,
            'artwork_cached': False
        }
        db.get_episodes.return_value = ([], 5)

        # First call returns 304, second call (forced full fetch) returns content
        rss_parser.fetch_feed_conditional.side_effect = [
            (None, '"abc123"', None),      # 304
            ('<rss>full</rss>', '"abc123"', None)  # forced full fetch
        ]

        storage.load_data_json.return_value = {'feed_url': 'https://example.com/rss'}

        # The full fetch path needs parsed_feed
        parsed_feed = MagicMock()
        parsed_feed.feed.get.side_effect = lambda k, default='': 'Test Podcast' if k == 'title' else default
        parsed_feed.entries = []
        rss_parser.parse_feed.return_value = parsed_feed
        rss_parser.modify_feed.return_value = '<rss>modified</rss>'
        db.get_processed_episodes_for_feed.return_value = []

        refresh_rss_feed('test-podcast', 'https://example.com/rss')

        # Should have done a full fetch (second call to fetch_feed_conditional with no etag)
        self.assertEqual(rss_parser.fetch_feed_conditional.call_count, 2)
        second_call = rss_parser.fetch_feed_conditional.call_args_list[1]
        self.assertIsNone(second_call[1].get('etag'))


if __name__ == '__main__':
    unittest.main()
