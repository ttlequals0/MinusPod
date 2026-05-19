"""Tests for the User-Agent header on the initial RSS fetch.

Some feed hosts (notably feeds.podcastindex.org) reject the default
python-requests UA with 403. ``fetch_feed_conditional`` has always
passed ``APP_USER_AGENT``; ``fetch_feed`` did not, which made
``add_feed`` slug derivation silently fail on UA-strict hosts because
the title-fetch step returned None and the endpoint then refused to
auto-derive a slug from the URL.
"""
from unittest.mock import MagicMock, patch

import defusedxml
defusedxml.defuse_stdlib()

from config import APP_USER_AGENT
from rss_parser import RSSParser


def _ok_response(body: bytes = b"<rss><channel><title>X</title></channel></rss>"):
    resp = MagicMock()
    resp.status_code = 200
    resp.headers = {'Content-Type': 'application/rss+xml'}
    resp.raise_for_status = MagicMock()
    resp.iter_content = MagicMock(return_value=iter([body]))
    resp.content = body
    resp.text = body.decode('utf-8')
    return resp


class TestFetchFeedSendsAppUserAgent:
    def test_initial_fetch_passes_app_user_agent(self):
        rp = RSSParser()
        with patch('rss_parser.safe_get') as mock_get:
            mock_get.return_value = _ok_response()
            with patch('rss_parser.read_response_capped', return_value=b"<rss/>"):
                rp.fetch_feed('https://example.com/feed.xml')

        assert mock_get.called
        _, kwargs = mock_get.call_args
        headers = kwargs.get('headers') or {}
        assert headers.get('User-Agent') == APP_USER_AGENT, (
            "fetch_feed must pass APP_USER_AGENT to safe_get; otherwise "
            "UA-strict feed hosts (e.g. feeds.podcastindex.org) reject the "
            "request with 403 and slug derivation fails."
        )

    def test_gzip_retry_also_passes_user_agent(self):
        """The gzip-fallback retry path inside fetch_feed must keep the UA."""
        import requests

        rp = RSSParser()
        captured_headers = []

        def fake_safe_get(*args, **kwargs):
            captured_headers.append(kwargs.get('headers') or {})
            if len(captured_headers) == 1:
                # First call: simulate the gzip-decode error.
                raise requests.exceptions.ContentDecodingError("bad gzip")
            return _ok_response()

        with patch('rss_parser.safe_get', side_effect=fake_safe_get):
            with patch('rss_parser.read_response_capped', return_value=b"<rss/>"):
                rp.fetch_feed('https://example.com/feed.xml')

        assert len(captured_headers) == 2
        # Both attempts carry the project UA; retry must not regress this.
        assert captured_headers[0].get('User-Agent') == APP_USER_AGENT
        assert captured_headers[1].get('User-Agent') == APP_USER_AGENT
        # Retry also forces identity encoding.
        assert captured_headers[1].get('Accept-Encoding') == 'identity'
