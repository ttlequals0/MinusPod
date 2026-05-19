"""Tests for RSSParser.extract_podcast_artwork_url.

The Podcasting 2.0 reference feed pc20.xml exposes a known feedparser
quirk: it declares a 144x144 PNG as the channel ``<image><url>`` and as
the channel ``<itunes:image href="...">``, but the FIRST episode also
declares its own ``<itunes:image>`` (a 40 MB animated GIF), and
feedparser folds that into ``feed.image.href``. Reading the raw XML
channel-level elements directly avoids that override.
"""
import defusedxml
defusedxml.defuse_stdlib()

from rss_parser import RSSParser


def _feed(channel_inner: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
  <channel>
    <title>X</title>
    {channel_inner}
    <item>
      <title>Ep 1</title>
      <itunes:image href="https://example.com/PER-EPISODE-OVERRIDE.gif"/>
      <enclosure url="https://example.com/ep1.mp3" type="audio/mpeg"/>
    </item>
  </channel>
</rss>"""


class TestRawXmlExtraction:
    def test_prefers_channel_itunes_image_over_rss_image(self):
        feed = _feed("""
            <itunes:image href="https://example.com/channel-itunes.png"/>
            <image><url>https://example.com/channel-rss.png</url></image>
        """)
        assert RSSParser.extract_podcast_artwork_url(feed) == "https://example.com/channel-itunes.png"

    def test_falls_back_to_rss_image_when_no_channel_itunes_image(self):
        feed = _feed("""<image><url>https://example.com/only-rss.png</url></image>""")
        assert RSSParser.extract_podcast_artwork_url(feed) == "https://example.com/only-rss.png"

    def test_ignores_per_episode_itunes_image(self):
        # No channel-level image at all; per-episode override must NOT leak in.
        feed = _feed("")
        assert RSSParser.extract_podcast_artwork_url(feed) is None

    def test_pc20_shape_returns_channel_png_not_episode_gif(self):
        # Mirrors the real pc20.xml shape that exposes the feedparser bug.
        feed = _feed("""
            <itunes:image href="https://noagendaassets.com/enc/pc20-channel.png"/>
            <image>
              <url>https://noagendaassets.com/enc/pc20-channel.png</url>
              <title>Podcasting 2.0</title>
              <link>http://podcastindex.org</link>
            </image>
        """)
        result = RSSParser.extract_podcast_artwork_url(feed)
        assert result == "https://noagendaassets.com/enc/pc20-channel.png"
        assert "PER-EPISODE-OVERRIDE" not in (result or "")

    def test_accepts_bytes(self):
        feed = _feed("""<image><url>https://example.com/bytes.png</url></image>""")
        assert RSSParser.extract_podcast_artwork_url(feed.encode('utf-8')) == "https://example.com/bytes.png"

    def test_malformed_xml_returns_none(self):
        assert RSSParser.extract_podcast_artwork_url("<<not really xml>>") is None

    def test_empty_input_returns_none(self):
        assert RSSParser.extract_podcast_artwork_url("") is None
        assert RSSParser.extract_podcast_artwork_url(None) is None


class TestModifiedFeedEmitsChannelArtwork:
    def test_modify_feed_emits_channel_itunes_image_with_correct_url(self):
        upstream = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
  <channel>
    <title>Channel</title>
    <link>https://example.com</link>
    <description>D</description>
    <itunes:image href="https://example.com/correct.png"/>
    <image><url>https://example.com/correct.png</url></image>
    <item>
      <title>Ep</title>
      <itunes:image href="https://example.com/PER-EPISODE.gif"/>
      <enclosure url="https://example.com/ep.mp3" type="audio/mpeg"/>
    </item>
  </channel>
</rss>"""
        served = RSSParser(base_url="https://mp.example.com").modify_feed(upstream, "slug")
        # Channel-level itunes:image present at channel scope (before the first <item>).
        first_item_idx = served.find("<item>")
        channel_block = served[:first_item_idx]
        assert '<itunes:image href="https://example.com/correct.png" />' in channel_block
        assert '<url>https://example.com/correct.png</url>' in channel_block
        # And the per-episode override must NOT have leaked into the channel block.
        assert "PER-EPISODE.gif" not in channel_block
