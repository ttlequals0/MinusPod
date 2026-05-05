"""Tests for issue #193: BASE_URL must be re-resolved per modify_feed call.

The module-level RSSParser is built once at gunicorn boot, so an operator
who fixes BASE_URL after boot would never see new enclosure URLs unless
modify_feed re-reads the env. An explicit base_url= injected by a caller
(or by tests) must continue to override the env.
"""

import re

import pytest

import defusedxml
defusedxml.defuse_stdlib()

from rss_parser import RSSParser, extract_cached_base_url


def _build_rss():
    return """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
    <channel>
        <title>Test Podcast</title>
        <link>https://example.com</link>
        <description>For testing</description>
        <item>
            <title>Episode A</title>
            <guid>guid-a</guid>
            <pubDate>Wed, 01 Jan 2025 00:00:00 +0000</pubDate>
            <enclosure url="https://cdn.example.com/a.mp3" type="audio/mpeg" length="100" />
        </item>
    </channel>
</rss>
"""


def _enclosure_prefix(rss_xml: str) -> str:
    m = re.search(r'<enclosure url="([^"]+)/episodes/', rss_xml)
    assert m, f"no rewritten enclosure URL in:\n{rss_xml}"
    return m.group(1)


class TestBaseUrlRefresh:
    def test_modify_feed_picks_up_base_url_change(self, monkeypatch):
        """A parser built with one BASE_URL must reflect a later env change."""
        monkeypatch.setenv("BASE_URL", "http://a.test")
        parser = RSSParser()
        assert parser.base_url == "http://a.test"

        # Operator fixes the env, then a feed render happens.
        monkeypatch.setenv("BASE_URL", "http://b.test")
        result = parser.modify_feed(_build_rss(), "test-pod")
        assert _enclosure_prefix(result) == "http://b.test"

    def test_modify_feed_respects_explicit_base_url_over_env(self, monkeypatch):
        """An explicit base_url= injection wins over env on every render."""
        parser = RSSParser(base_url="http://injected.test")
        monkeypatch.setenv("BASE_URL", "http://env.test")

        result = parser.modify_feed(_build_rss(), "test-pod")
        assert _enclosure_prefix(result) == "http://injected.test"

    def test_default_when_env_unset_falls_back_to_localhost(self, monkeypatch):
        """No injection + no env should still produce a usable URL."""
        monkeypatch.delenv("BASE_URL", raising=False)
        parser = RSSParser()
        result = parser.modify_feed(_build_rss(), "test-pod")
        assert _enclosure_prefix(result) == "http://localhost:8000"


class TestExtractCachedBaseUrl:
    def test_extracts_prefix_from_rendered_rss(self):
        parser = RSSParser(base_url="https://feed.example.test")
        rendered = parser.modify_feed(_build_rss(), "test-pod")
        assert extract_cached_base_url(rendered) == "https://feed.example.test"

    def test_returns_none_when_no_enclosure(self):
        assert extract_cached_base_url("<rss><channel></channel></rss>") is None
