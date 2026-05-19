"""Regression guard: the served feed MUST NOT advertise upstream URLs
in `<podcast:transcript>` or `<podcast:chapters>` tags.

Background: 2.5.4 introduced a per-episode passthrough that emitted
upstream publisher URLs for any episode MinusPod had not yet
processed. This violated the core MinusPod contract (subscribers must
reach MinusPod for all content, never the publisher). 2.5.5 reverted
that change. These tests fail loudly if the regression is reintroduced.
"""
import re
from unittest.mock import MagicMock
from urllib.parse import urlparse

import defusedxml
defusedxml.defuse_stdlib()

from rss_parser import RSSParser


UPSTREAM_NETLOC = "upstream.example.com"
MINUSPOD_NETLOC = "mp.example.com"
UPSTREAM_TRANSCRIPT_URL = f"https://{UPSTREAM_NETLOC}/episode-1.srt"
UPSTREAM_CHAPTERS_URL = f"https://{UPSTREAM_NETLOC}/episode-1.json"


def _netloc(url: str) -> str:
    return urlparse(url).netloc


def _feed_with_upstream_pc2_tags() -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"
     xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"
     xmlns:podcast="https://podcastindex.org/namespace/1.0">
  <channel>
    <title>Test</title>
    <link>https://upstream.example.com</link>
    <description>D</description>
    <language>en</language>
    <item>
      <title>Ep 1</title>
      <enclosure url="https://upstream.example.com/ep1.mp3" type="audio/mpeg"/>
      <guid>ep1</guid>
      <podcast:transcript url="{UPSTREAM_TRANSCRIPT_URL}" type="application/srt" rel="captions"/>
      <podcast:chapters url="{UPSTREAM_CHAPTERS_URL}" type="application/json+chapters"/>
    </item>
    <item>
      <title>Ep 2</title>
      <enclosure url="https://upstream.example.com/ep2.mp3" type="audio/mpeg"/>
      <guid>ep2</guid>
      <podcast:transcript url="https://upstream.example.com/episode-2.srt" type="application/srt"/>
      <podcast:chapters url="https://upstream.example.com/episode-2.json" type="application/json+chapters"/>
    </item>
  </channel>
</rss>"""


def _serve(*, has_vtt: bool = False, has_chapters: bool = False) -> str:
    storage = MagicMock()
    storage.has_transcript_vtt = MagicMock(return_value=has_vtt)
    storage.has_chapters_json = MagicMock(return_value=has_chapters)
    return RSSParser(base_url="https://mp.example.com").modify_feed(
        _feed_with_upstream_pc2_tags(), "slug", storage=storage
    )


class TestUnprocessedEpisodeDoesNotLeakUpstream:
    def test_no_podcast_transcript_when_storage_has_nothing(self):
        out = _serve(has_vtt=False, has_chapters=False)
        assert "<podcast:transcript" not in out
        assert "<podcast:chapters" not in out

    def test_upstream_urls_never_appear_in_output(self):
        out = _serve(has_vtt=False, has_chapters=False)
        # Scan ALL podcast:transcript/podcast:chapters attribute URLs and
        # confirm none have an upstream netloc. Parsed equality avoids the
        # substring-containment pitfall flagged by CodeQL on the earlier
        # 2.5.4 test file (alert: incomplete URL substring sanitization).
        for url in re.findall(r'<podcast:transcript[^>]*url="([^"]+)"', out):
            assert _netloc(url) != UPSTREAM_NETLOC, f"transcript leak: {url}"
        for url in re.findall(r'<podcast:chapters[^>]*url="([^"]+)"', out):
            assert _netloc(url) != UPSTREAM_NETLOC, f"chapters leak: {url}"

    def test_storage_is_none_emits_neither_tag(self):
        # Edge: storage=None should also produce zero passthrough.
        served = RSSParser(base_url="https://mp.example.com").modify_feed(
            _feed_with_upstream_pc2_tags(), "slug", storage=None
        )
        assert "<podcast:transcript" not in served
        assert "<podcast:chapters" not in served


class TestProcessedEpisodeEmitsMinusPodUrl:
    def test_cached_vtt_emits_minuspod_url_not_upstream(self):
        out = _serve(has_vtt=True, has_chapters=False)
        urls = re.findall(r'<podcast:transcript[^>]*url="([^"]+)"', out)
        assert len(urls) >= 1
        for url in urls:
            assert _netloc(url) == MINUSPOD_NETLOC, f"tag did not point at MinusPod: {url}"
            assert _netloc(url) != UPSTREAM_NETLOC

    def test_cached_chapters_emits_minuspod_url_not_upstream(self):
        out = _serve(has_vtt=False, has_chapters=True)
        urls = re.findall(r'<podcast:chapters[^>]*url="([^"]+)"', out)
        assert len(urls) >= 1
        for url in urls:
            assert _netloc(url) == MINUSPOD_NETLOC, f"tag did not point at MinusPod: {url}"
            assert _netloc(url) != UPSTREAM_NETLOC
