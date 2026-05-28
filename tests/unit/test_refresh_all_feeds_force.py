"""Tests for force propagation in refresh_all_feeds.

The /feeds/refresh API handler accepts {"force": true} and must thread the
flag down to refresh_rss_feed so the per-feed 30-second coalesce window is
bypassed. Without this, clicking "Force Refresh All" in the UI is a silent
no-op for any feed refreshed in the last 30s.
"""
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

_test_data_dir = tempfile.mkdtemp(prefix='refresh_all_force_test_')
os.environ.setdefault('SECRET_KEY', 'test-secret')
os.environ['DATA_DIR'] = _test_data_dir

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

import database
import storage as storage_mod
database.Database._instance = None
database.Database.__init__.__defaults__ = (_test_data_dir,)
database.Database.__new__.__defaults__ = (_test_data_dir,)
storage_mod.Storage.__init__.__defaults__ = (_test_data_dir,)

from main_app.feeds import refresh_all_feeds
import main_app.feeds as _feeds_module


class TestRefreshAllFeedsForce(unittest.TestCase):

    def setUp(self):
        _feeds_module._refresh_coalesce.invalidate()

    @patch('main_app.feeds.refresh_rss_feed')
    @patch('main_app.feeds.get_feed_map')
    def test_default_call_passes_force_false(self, get_feed_map, refresh_rss_feed):
        get_feed_map.return_value = {
            'pod-a': {'in': 'https://example.com/a.rss'},
            'pod-b': {'in': 'https://example.com/b.rss'},
        }
        refresh_rss_feed.return_value = True

        refresh_all_feeds()

        self.assertEqual(refresh_rss_feed.call_count, 2)
        for call in refresh_rss_feed.call_args_list:
            args, kwargs = call
            # Executor invokes positionally: (slug, feed_url, force)
            self.assertEqual(args[2], False)

    @patch('main_app.feeds.refresh_rss_feed')
    @patch('main_app.feeds.get_feed_map')
    def test_force_true_propagates_to_every_feed(self, get_feed_map, refresh_rss_feed):
        get_feed_map.return_value = {
            'pod-a': {'in': 'https://example.com/a.rss'},
            'pod-b': {'in': 'https://example.com/b.rss'},
            'pod-c': {'in': 'https://example.com/c.rss'},
        }
        refresh_rss_feed.return_value = True

        refresh_all_feeds(force=True)

        self.assertEqual(refresh_rss_feed.call_count, 3)
        for call in refresh_rss_feed.call_args_list:
            args, kwargs = call
            self.assertEqual(args[2], True)


class TestRefreshRSSFeedCoalesceBypass(unittest.TestCase):
    """Regression: force=True must bypass the 30s _refresh_coalesce gate."""

    def setUp(self):
        _feeds_module._refresh_coalesce.invalidate()

    @patch('main_app.feeds.pattern_service')
    @patch('main_app.feeds.status_service')
    @patch('main_app.feeds.storage')
    @patch('main_app.feeds.rss_parser')
    @patch('main_app.feeds.db')
    def test_force_true_runs_even_when_recently_refreshed(
        self, db, rss_parser, status_service, storage, pattern_service
    ):
        from main_app.feeds import refresh_rss_feed

        _feeds_module._refresh_coalesce.set('pod-a', True)

        db.get_podcast_by_slug.return_value = {
            'id': 1, 'feed_url': 'https://example.com/a.rss',
            'etag': '"abc"', 'last_modified': None, 'artwork_cached': True,
        }
        db.get_episodes.return_value = ([], 0)
        rss_parser.fetch_feed_conditional.return_value = (None, '"abc"', None)
        storage.load_data_json.return_value = {'feed_url': 'https://example.com/a.rss'}

        refresh_rss_feed('pod-a', 'https://example.com/a.rss', force=True)

        # Coalesce gate is bypassed -> fetch actually ran.
        # (The function may retry once on 304+no-episodes; that's not what we're testing.)
        self.assertGreaterEqual(rss_parser.fetch_feed_conditional.call_count, 1)
        first_call_kwargs = rss_parser.fetch_feed_conditional.call_args_list[0].kwargs
        self.assertIsNone(first_call_kwargs.get('etag'))
        self.assertIsNone(first_call_kwargs.get('last_modified'))

    @patch('main_app.feeds.pattern_service')
    @patch('main_app.feeds.status_service')
    @patch('main_app.feeds.storage')
    @patch('main_app.feeds.rss_parser')
    @patch('main_app.feeds.db')
    def test_force_false_short_circuits_when_recently_refreshed(
        self, db, rss_parser, status_service, storage, pattern_service
    ):
        from main_app.feeds import refresh_rss_feed

        _feeds_module._refresh_coalesce.set('pod-a', True)

        refresh_rss_feed('pod-a', 'https://example.com/a.rss', force=False)

        rss_parser.fetch_feed_conditional.assert_not_called()

    @patch('main_app.feeds.pattern_service')
    @patch('main_app.feeds.status_service')
    @patch('main_app.feeds.storage')
    @patch('main_app.feeds.rss_parser')
    @patch('main_app.feeds.db')
    def test_force_true_clears_etag_when_upstream_drops_header(
        self, db, rss_parser, status_service, storage, pattern_service
    ):
        """Regression: a force-refresh against an upstream that returns 200 OK
        without ETag/Last-Modified headers must overwrite the stored ETag with
        None, otherwise the next conditional GET sends a stale validator."""
        from main_app.feeds import refresh_rss_feed

        db.get_podcast_by_slug.return_value = {
            'id': 1, 'feed_url': 'https://example.com/a.rss',
            'etag': '"stale-etag"', 'last_modified': None, 'artwork_cached': True,
        }
        db.get_episodes.return_value = ([], 0)
        rss_parser.fetch_feed_conditional.return_value = (
            b'<rss><channel><title>x</title></channel></rss>', None, None,
        )
        mock_parsed = MagicMock()
        mock_parsed.feed = {'title': 'x', 'description': ''}
        mock_parsed.entries = []
        rss_parser.parse_feed.return_value = mock_parsed
        rss_parser.extract_podcast_artwork_url.return_value = None
        rss_parser.extract_podcast_categories.return_value = []
        storage.load_data_json.return_value = {'feed_url': 'https://example.com/a.rss'}

        refresh_rss_feed('pod-a', 'https://example.com/a.rss', force=True)

        update_kwargs = db.update_podcast.call_args.kwargs
        self.assertIn('etag', update_kwargs)
        self.assertIsNone(update_kwargs['etag'])
        self.assertIsNone(update_kwargs['last_modified_header'])


if __name__ == '__main__':
    unittest.main()
