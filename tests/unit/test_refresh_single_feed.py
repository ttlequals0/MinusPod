"""Tests for refresh_single_feed."""
import unittest
from unittest.mock import patch

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('refresh_single_feed_test_')

from main_app.feeds import refresh_single_feed


class TestRefreshSingleFeed(unittest.TestCase):

    @patch('main_app.feeds.db')
    @patch('main_app.feeds.refresh_rss_feed')
    def test_refresh_single_feed_calls_refresh_rss_feed(self, mock_refresh, mock_db):
        """Test that refresh_single_feed looks up a podcast and calls refresh_rss_feed."""
        mock_refresh.return_value = True
        slug = 'demo'
        mock_db.get_podcast_by_slug.return_value = {
            'slug': slug,
            'source_url': 'https://example.com/feed.xml',
            'title': 'Demo Podcast'
        }

        result = refresh_single_feed(slug)

        assert result is True
        mock_db.get_podcast_by_slug.assert_called_once_with(slug)
        mock_refresh.assert_called_once_with(slug, 'https://example.com/feed.xml')

    @patch('main_app.feeds.db')
    @patch('main_app.feeds.refresh_rss_feed')
    def test_refresh_single_feed_unknown_slug_returns_false(self, mock_refresh, mock_db):
        """Test that refresh_single_feed returns False for unknown slug."""
        mock_db.get_podcast_by_slug.return_value = None

        result = refresh_single_feed('nope-does-not-exist')

        assert result is False
        mock_refresh.assert_not_called()

    @patch('main_app.feeds.db')
    @patch('main_app.feeds.refresh_rss_feed')
    def test_refresh_single_feed_missing_source_url_returns_false(self, mock_refresh, mock_db):
        """Test that refresh_single_feed returns False when source_url is missing."""
        slug = 'bad-feed'
        mock_db.get_podcast_by_slug.return_value = {'slug': slug, 'source_url': None}

        result = refresh_single_feed(slug)

        assert result is False
        mock_refresh.assert_not_called()

    @patch('main_app.feeds.refresh_logger')
    @patch('main_app.feeds.db')
    @patch('main_app.feeds.refresh_rss_feed')
    def test_refresh_single_feed_logs_exception(self, mock_refresh, mock_db, mock_logger):
        """Test that refresh_single_feed logs exceptions."""
        slug = 'demo'
        mock_db.get_podcast_by_slug.return_value = {
            'slug': slug,
            'source_url': 'https://example.com/feed.xml'
        }
        mock_refresh.side_effect = Exception('Feed error')

        result = refresh_single_feed(slug)

        assert result is False
        mock_logger.error.assert_called_once()
        assert slug in str(mock_logger.error.call_args)
