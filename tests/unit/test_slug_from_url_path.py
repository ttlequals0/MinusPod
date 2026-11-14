"""URL-path slug fallback used by add_feed and OPML import.

Used when neither an OPML title nor an RSS ``<title>`` is available (e.g. the host blocks the initial fetch).
"""
import pytest

from api.feeds import _slug_from_url_path


class TestUrlPathSlug:
    @pytest.mark.parametrize("url, expected", [
        ("https://feeds.podcastindex.org/pc20.xml", "pc20"),
        ("https://feeds.podcastindex.org/pc20.rss", "pc20"),
        ("https://example.com/path/to/myshow.xml", "myshow"),
        ("https://example.com/show/", "show"),
        # No path at all: fall back to netloc; slugify lowercases and
        # turns the dot into a hyphen.
        ("https://example.com", "example-com"),
        ("https://example.com/", "example-com"),
    ])
    def test_returns_slug_for_common_shapes(self, url, expected):
        assert _slug_from_url_path(url) == expected

    def test_strips_xml_and_rss_extensions(self):
        assert _slug_from_url_path("https://x/feed.xml") == "feed"
        assert _slug_from_url_path("https://x/feed.rss") == "feed"

    def test_slug_is_safe_kebab(self):
        # Slug library normalizes underscores and case.
        out = _slug_from_url_path("https://example.com/Some_Show_Name")
        assert out == "some-show-name"
