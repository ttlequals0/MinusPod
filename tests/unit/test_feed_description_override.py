"""Per-feed description override on the served channel <description>."""
from defusedxml import ElementTree as ET

from rss_parser import RSSParser

FEED = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
  <channel>
    <title>Source Show</title>
    <link>https://example.com</link>
    <description>Source blurb</description>
    <language>en</language>
    <item>
      <title>Ep One</title>
      <description>Episode blurb</description>
      <enclosure url="https://example.com/ep.mp3" type="audio/mpeg"/>
      <guid>ep1</guid>
    </item>
  </channel>
</rss>"""


def _channel_description(description_override=None):
    served = RSSParser(base_url="https://mp.example.com").modify_feed(
        FEED, "slug", description_override=description_override)
    return ET.fromstring(served).find('channel/description').text


def test_override_replaces_channel_description():
    assert _channel_description("Hand written") == "Hand written"


def test_no_override_keeps_source_description():
    assert _channel_description() == "Source blurb"
    assert _channel_description("   ") == "Source blurb"


def test_override_is_sanitized_like_the_source_value():
    text = _channel_description("Hi<script>alert(1)</script> there")
    assert "<script" not in text
    assert "Hi" in text and "there" in text


def test_override_does_not_touch_episode_description():
    served = RSSParser(base_url="https://mp.example.com").modify_feed(
        FEED, "slug", description_override="Hand written")
    assert "Episode blurb" in served
