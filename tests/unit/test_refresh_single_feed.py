"""Tests for refresh_single_feed."""
from unittest.mock import patch

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('refresh_single_feed_test_')

from main_app.feeds import refresh_single_feed
from api import get_database


def test_refresh_single_feed_calls_refresh_rss_feed():
    """Test that refresh_single_feed looks up a podcast and calls refresh_rss_feed."""
    db = get_database()
    slug = 'demo'
    db.create_podcast(slug, 'https://example.com/feed.xml', 'Demo Podcast')

    with patch('main_app.feeds.refresh_rss_feed') as m:
        m.return_value = True
        result = refresh_single_feed(slug)
        assert result is True
        m.assert_called_once()
        args, kwargs = m.call_args
        assert args[0] == slug
        assert args[1].startswith('http')

    db.delete_podcast(slug)


def test_refresh_single_feed_unknown_slug_returns_false():
    """Test that refresh_single_feed returns False for unknown slug."""
    result = refresh_single_feed('nope-does-not-exist')
    assert result is False
