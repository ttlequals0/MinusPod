"""Tests for the issue #181 processed_only filter on RSSParser.modify_feed.

When processed_only=True, upstream RSS items whose generated episode_id is
not in processed_episode_ids must be dropped before URL rewrite. When
processed_only=False (default), behavior must be identical to pre-#181.
"""

import re

import pytest

import defusedxml
defusedxml.defuse_stdlib()

from rss_parser import RSSParser


def _build_rss(entries):
    """Build a minimal RSS 2.0 feed from (title, guid, audio_url) tuples."""
    items = "\n".join(
        f"""
        <item>
            <title>{title}</title>
            <guid>{guid}</guid>
            <pubDate>Wed, 01 Jan 2025 00:00:00 +0000</pubDate>
            <enclosure url="{audio_url}" type="audio/mpeg" length="100" />
        </item>
        """
        for title, guid, audio_url in entries
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
    <channel>
        <title>Test Podcast</title>
        <link>https://example.com</link>
        <description>For testing</description>
        {items}
    </channel>
</rss>
"""


@pytest.fixture
def parser():
    return RSSParser(base_url="https://podsrv.example.test")


@pytest.fixture
def feed_with_three_entries():
    """Three episodes with stable, distinct GUIDs."""
    return _build_rss([
        ("Episode A", "guid-a", "https://cdn.example.com/a.mp3"),
        ("Episode B", "guid-b", "https://cdn.example.com/b.mp3"),
        ("Episode C", "guid-c", "https://cdn.example.com/c.mp3"),
    ])


def _episode_ids_in(parser, entries):
    """Compute the deterministic episode_ids for the test fixture entries."""
    return [parser.generate_episode_id(url, guid) for _, guid, url in entries]


class TestProcessedOnlyFilter:
    def test_default_behavior_includes_all_entries(self, parser, feed_with_three_entries):
        """Regression guard: processed_only defaults to False; output identical to pre-#181."""
        result = parser.modify_feed(feed_with_three_entries, "test-pod")
        # All three rewritten enclosure URLs must be present.
        rewritten = re.findall(r'/episodes/test-pod/([0-9a-f]+)\.mp3', result)
        assert len(rewritten) == 3

    def test_explicit_false_includes_all_entries(self, parser, feed_with_three_entries):
        """processed_only=False with a populated allow-list still includes everything."""
        entries = [
            ("Episode A", "guid-a", "https://cdn.example.com/a.mp3"),
            ("Episode B", "guid-b", "https://cdn.example.com/b.mp3"),
            ("Episode C", "guid-c", "https://cdn.example.com/c.mp3"),
        ]
        ids = _episode_ids_in(parser, entries)
        result = parser.modify_feed(
            feed_with_three_entries, "test-pod",
            processed_only=False, processed_episode_ids={ids[0]},
        )
        rewritten = re.findall(r'/episodes/test-pod/([0-9a-f]+)\.mp3', result)
        assert len(rewritten) == 3

    def test_processed_only_drops_unlisted_entries(self, parser, feed_with_three_entries):
        """processed_only=True keeps only entries whose episode_id is in the allow-list."""
        entries = [
            ("Episode A", "guid-a", "https://cdn.example.com/a.mp3"),
            ("Episode B", "guid-b", "https://cdn.example.com/b.mp3"),
            ("Episode C", "guid-c", "https://cdn.example.com/c.mp3"),
        ]
        ids = _episode_ids_in(parser, entries)
        # Only 'B' is processed.
        result = parser.modify_feed(
            feed_with_three_entries, "test-pod",
            processed_only=True, processed_episode_ids={ids[1]},
        )
        rewritten = re.findall(r'/episodes/test-pod/([0-9a-f]+)\.mp3', result)
        assert rewritten == [ids[1]]

    def test_processed_only_with_empty_allow_list_yields_no_items(self, parser, feed_with_three_entries):
        """processed_only=True with an empty set drops every upstream item; channel metadata stays."""
        result = parser.modify_feed(
            feed_with_three_entries, "test-pod",
            processed_only=True, processed_episode_ids=set(),
        )
        # No rewritten enclosures at all.
        assert "/episodes/test-pod/" not in result
        # Channel-level metadata still rendered (basic sanity).
        assert "<channel>" in result
        assert "</channel>" in result

    def test_processed_only_without_allow_list_treats_as_empty(self, parser, feed_with_three_entries):
        """processed_only=True with processed_episode_ids=None must not crash and drops all entries."""
        result = parser.modify_feed(
            feed_with_three_entries, "test-pod",
            processed_only=True, processed_episode_ids=None,
        )
        assert "/episodes/test-pod/" not in result
