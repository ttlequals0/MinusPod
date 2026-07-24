"""Tests for refresh_single_feed."""
import unittest
from unittest.mock import patch

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('refresh_single_feed_test_')

from main_app.feeds import refresh_single_feed
from api import get_database


class TestRefreshSingleFeed(unittest.TestCase):

    def setUp(self):
        self.db = get_database()

    def tearDown(self):
        # Clean up any created podcasts
        for podcast in self.db.get_feeds_config():
            slug = podcast.get('out', '').strip('/')
            if slug.startswith('test-'):
                self.db.delete_podcast(slug)

    @patch('main_app.feeds.refresh_rss_feed')
    def test_refresh_single_feed_calls_refresh_rss_feed(self, mock_refresh):
        """Test that refresh_single_feed looks up a podcast and calls refresh_rss_feed."""
        mock_refresh.return_value = True
        slug = 'test-demo'
        self.db.create_podcast(slug, 'https://example.com/feed.xml', 'Demo Podcast')

        result = refresh_single_feed(slug)

        assert result is True
        # Find the call with matching slug (filter out any other calls)
        matching_calls = [c for c in mock_refresh.call_args_list
                          if c[0][0] == slug]
        assert len(matching_calls) >= 1
        args = matching_calls[-1][0]
        assert args[0] == slug
        assert args[1].startswith('http')

    @patch('main_app.feeds.refresh_rss_feed')
    def test_refresh_single_feed_unknown_slug_returns_false(self, mock_refresh):
        """Test that refresh_single_feed returns False for unknown slug."""
        result = refresh_single_feed('nope-does-not-exist')
        assert result is False
