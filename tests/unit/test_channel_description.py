"""Tests for RSSParser._get_channel_description fallback chain.

Mirrors the episode-level subtitle/content fallback at channel scope.
Falls back only when ``<description>`` is empty or whitespace, never
"short": a publisher who emits a deliberately concise description
should not be second-guessed.
"""
import defusedxml
defusedxml.defuse_stdlib()

from rss_parser import RSSParser


class TestGetChannelDescription:
    def test_returns_description_when_present(self):
        channel = {"description": "Channel summary"}
        assert RSSParser._get_channel_description(channel) == "Channel summary"

    def test_falls_back_to_itunes_summary_when_description_empty(self):
        channel = {"description": "", "itunes_summary": "iTunes summary text"}
        assert RSSParser._get_channel_description(channel) == "iTunes summary text"

    def test_falls_back_through_subtitle_when_no_summary(self):
        channel = {"description": "", "itunes_summary": "", "subtitle": "Subtitle"}
        assert RSSParser._get_channel_description(channel) == "Subtitle"

    def test_falls_back_to_itunes_subtitle_last(self):
        channel = {
            "description": "",
            "itunes_summary": "",
            "subtitle": "",
            "itunes_subtitle": "iTunes subtitle",
        }
        assert RSSParser._get_channel_description(channel) == "iTunes subtitle"

    def test_returns_empty_when_all_absent(self):
        assert RSSParser._get_channel_description({}) == ""

    def test_whitespace_only_description_is_treated_as_empty(self):
        channel = {"description": "   \n  ", "itunes_summary": "real"}
        assert RSSParser._get_channel_description(channel) == "real"

    def test_short_but_real_description_is_kept(self):
        # Publisher emitted a deliberate 64-char description: respect it.
        channel = {
            "description": "The Podcast Index presents Podcasting 2.0 - Upgrading Podcasting",
            "itunes_summary": "Much longer iTunes summary should not override the short description",
        }
        result = RSSParser._get_channel_description(channel)
        assert result.startswith("The Podcast Index presents")
        assert "Much longer" not in result

    def test_handles_none_values(self):
        channel = {"description": None, "itunes_summary": None, "subtitle": "fallback"}
        assert RSSParser._get_channel_description(channel) == "fallback"


class TestChannelDescriptionInServedFeed:
    """End-to-end: when upstream <description> is empty, the served feed
    renders the iTunes summary inside the channel <description> CDATA."""

    def test_modify_feed_emits_itunes_summary_when_description_empty(self):
        upstream = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"
     xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"
     xmlns:podcast="https://podcastindex.org/namespace/1.0">
  <channel>
    <title>Sparse Show</title>
    <link>https://example.com</link>
    <description></description>
    <itunes:summary>This is the iTunes summary that must surface in the served feed.</itunes:summary>
    <language>en</language>
    <item>
      <title>Ep 1</title>
      <enclosure url="https://example.com/ep1.mp3" type="audio/mpeg"/>
      <guid>ep1</guid>
    </item>
  </channel>
</rss>"""
        served = RSSParser(base_url="https://mp.example.com").modify_feed(upstream, "sparse-show")
        assert "This is the iTunes summary that must surface in the served feed." in served
        # The channel description block must contain the summary text, not be empty CDATA.
        import re
        m = re.search(r"<description><!\[CDATA\[(.*?)\]\]></description>", served, re.DOTALL)
        assert m is not None
        first_channel_description = m.group(1)
        assert "iTunes summary" in first_channel_description
