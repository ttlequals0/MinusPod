"""Per-episode podcast:transcript / podcast:chapters passthrough.

When MinusPod has not yet processed an episode, the served enclosure
delivers the original upstream audio. The upstream publisher's
transcript and chapters are still aligned to that audio, so passing
them through is correct. Once MinusPod regenerates its own (cut-
aligned) version, ours takes precedence.
"""
from unittest.mock import MagicMock

import defusedxml
defusedxml.defuse_stdlib()

from rss_parser import RSSParser


def _feed_with_one_item(transcript_xml: str = "", chapters_xml: str = "") -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"
     xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"
     xmlns:podcast="https://podcastindex.org/namespace/1.0">
  <channel>
    <title>X</title>
    <link>https://x.com</link>
    <description>D</description>
    <language>en</language>
    <item>
      <title>Ep 1</title>
      <enclosure url="https://example.com/ep1.mp3" type="audio/mpeg"/>
      <guid>ep1</guid>
      {transcript_xml}
      {chapters_xml}
    </item>
  </channel>
</rss>"""


def _serve(feed: str, *, has_vtt=False, has_chapters=False) -> str:
    storage = MagicMock()
    storage.has_transcript_vtt = MagicMock(return_value=has_vtt)
    storage.has_chapters_json = MagicMock(return_value=has_chapters)
    return RSSParser(base_url="https://mp.example.com").modify_feed(feed, "slug", storage=storage)


class TestUnprocessedPassesUpstreamThrough:
    def test_transcript_passed_through_when_not_cached(self):
        feed = _feed_with_one_item(
            '<podcast:transcript url="https://up.example.com/t.srt" '
            'type="application/srt" rel="captions" language="en"/>'
        )
        out = _serve(feed, has_vtt=False)
        assert 'url="https://up.example.com/t.srt"' in out
        assert 'type="application/srt"' in out
        assert 'rel="captions"' in out
        assert 'language="en"' in out

    def test_chapters_passed_through_when_not_cached(self):
        feed = _feed_with_one_item(
            "", '<podcast:chapters url="https://up.example.com/ch.json" type="application/json+chapters"/>'
        )
        out = _serve(feed, has_chapters=False)
        assert 'url="https://up.example.com/ch.json"' in out
        assert 'type="application/json+chapters"' in out

    def test_multiple_transcripts_all_passed_through(self):
        feed = _feed_with_one_item(
            '<podcast:transcript url="https://up.example.com/t.srt" type="application/srt"/>'
            '<podcast:transcript url="https://up.example.com/t.vtt" type="text/vtt"/>'
        )
        out = _serve(feed, has_vtt=False)
        assert out.count("<podcast:transcript") == 2
        assert 't.srt' in out and 't.vtt' in out

    def test_no_transcript_no_chapters_when_neither_cached_nor_upstream(self):
        feed = _feed_with_one_item()
        out = _serve(feed, has_vtt=False, has_chapters=False)
        assert "<podcast:transcript" not in out
        assert "<podcast:chapters" not in out


class TestProcessedPrefersOurs:
    def test_cached_vtt_replaces_upstream_transcript(self):
        feed = _feed_with_one_item(
            '<podcast:transcript url="https://UP-URL.example.com/t.srt" '
            'type="application/srt"/>'
        )
        out = _serve(feed, has_vtt=True)
        # Our regenerated URL is emitted, NOT upstream's.
        assert "UP-URL.example.com" not in out
        assert "mp.example.com" in out  # our base URL
        # Exactly one transcript (ours), upstream not duplicated.
        assert out.count("<podcast:transcript") == 1

    def test_cached_chapters_replaces_upstream(self):
        feed = _feed_with_one_item(
            "", '<podcast:chapters url="https://UP-URL.example.com/ch.json" type="application/json+chapters"/>'
        )
        out = _serve(feed, has_chapters=True)
        assert "UP-URL.example.com" not in out
        assert out.count("<podcast:chapters") == 1


class TestAttributeEscaping:
    def test_upstream_url_with_ampersand_is_escaped(self):
        feed = _feed_with_one_item(
            '<podcast:transcript url="https://up.example.com/t.srt?a=1&amp;b=2" type="application/srt"/>'
        )
        out = _serve(feed, has_vtt=False)
        assert "a=1&amp;b=2" in out
        # Raw & must NOT appear inside the attribute value
        # (allow it in other places like CDATA descriptions)
        import re
        for m in re.finditer(r'<podcast:transcript[^>]+/>', out):
            assert "&amp;" in m.group(0) or "&" not in m.group(0)


class TestExtractorBuildsCorrectIndex:
    def test_extractor_returns_dict_keyed_by_enclosure_url(self):
        rp = RSSParser()
        feed = _feed_with_one_item(
            '<podcast:transcript url="https://up.example.com/t.srt" type="application/srt"/>'
            '<podcast:chapters url="https://up.example.com/ch.json" type="application/json+chapters"/>'
        )
        out = rp._extract_per_episode_pc2_tags(feed)
        assert "https://example.com/ep1.mp3" in out
        per_ep = out["https://example.com/ep1.mp3"]
        assert len(per_ep["transcript"]) == 1
        assert per_ep["transcript"][0][0] == "https://up.example.com/t.srt"
        assert per_ep["transcript"][0][1] == "application/srt"
        assert len(per_ep["chapters"]) == 1
        assert per_ep["chapters"][0][0] == "https://up.example.com/ch.json"

    def test_extractor_skips_items_with_neither(self):
        feed = _feed_with_one_item()
        out = RSSParser()._extract_per_episode_pc2_tags(feed)
        assert out == {}

    def test_extractor_handles_malformed_xml(self):
        assert RSSParser()._extract_per_episode_pc2_tags("<<not xml>>") == {}
        assert RSSParser()._extract_per_episode_pc2_tags("") == {}
        assert RSSParser()._extract_per_episode_pc2_tags(None) == {}
