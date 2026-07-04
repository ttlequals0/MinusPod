"""Served-RSS URL emission for authenticated feeds (2.33.0).

modify_feed(feed_auth_key=...) must key every fetchable URL (enclosure, vtt,
chapters, badged cover) while leaving the podcast:guid seed untouched, and
extract_cached_feed_auth_key must round-trip the rendered feed.
"""
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from rss_parser import RSSParser, extract_cached_feed_auth_key

BASE = 'https://mp.example.com'
KEY = 'c' * 64
VERSION = 'deadbeef'
ITUNES_NS = 'http://www.itunes.com/dtds/podcast-1.0.dtd'


class FakeStorage:
    """Just enough surface for modify_feed's storage probes."""

    def __init__(self, version=VERSION):
        self._version = version

    def has_artwork(self, slug):
        return True

    def artwork_version(self, slug):
        return self._version

    def has_transcript_vtt(self, slug, episode_id):
        return True

    def has_chapters_json(self, slug, episode_id):
        return True


def _feed_xml():
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="{ITUNES_NS}"
     xmlns:podcast="https://podcastindex.org/namespace/1.0">
  <channel>
    <title>Source Show</title>
    <link>https://example.com</link>
    <description>D</description>
    <language>en</language>
    <image>
      <url>https://example.com/art.png</url>
      <title>Source Show</title>
      <link>https://example.com</link>
    </image>
    <item>
      <title>Ep One</title>
      <enclosure url="https://example.com/ep1.mp3" type="audio/mpeg"/>
      <guid>ep1</guid>
    </item>
  </channel>
</rss>"""


def _serve(key=None, watermark=False, extra=None):
    return RSSParser(base_url=BASE).modify_feed(
        _feed_xml(), 'authfeed', storage=FakeStorage(),
        watermark_artwork=watermark, feed_auth_key=key,
        extra_episodes=extra or [])


def test_enclosure_vtt_chapters_carry_key():
    served = _serve(key=KEY)
    ep_id = re.search(r'/episodes/authfeed/([0-9a-f]{12})\.mp3', served).group(1)
    assert f'{BASE}/episodes/authfeed/{ep_id}.mp3?key={KEY}"' in served
    assert f'{BASE}/episodes/authfeed/{ep_id}.vtt?key={KEY}"' in served
    assert f'{BASE}/episodes/authfeed/{ep_id}/chapters.json?key={KEY}"' in served


def test_keyless_serving_has_no_key_params():
    served = _serve(key=None)
    assert '?key=' not in served


def test_db_appended_episode_carries_key():
    extra = [{'episode_id': 'f' * 12, 'title': 'Old Ep',
              'published_at': '2026-01-01T00:00:00+00:00',
              'new_duration': 60, 'episode_number': 1,
              'processed_version': 2}]
    served = _serve(key=KEY, extra=extra)
    assert f'{BASE}/episodes/authfeed/{"f" * 12}-v2.mp3?key={KEY}"' in served


def test_artwork_embeds_key_in_path_token():
    served = _serve(key=KEY, watermark=True)
    assert f'{BASE}/authfeed/cover-minuspod-{VERSION}-{KEY}.jpg' in served
    assert '.jpg?key=' not in served  # never a query string on the image


def test_artwork_key_without_version():
    served = RSSParser(base_url=BASE).modify_feed(
        _feed_xml(), 'authfeed', storage=FakeStorage(version=None),
        watermark_artwork=True, feed_auth_key=KEY)
    assert f'{BASE}/authfeed/cover-minuspod-{KEY}.jpg' in served


def test_podcast_guid_identical_with_and_without_key():
    guid_re = re.compile(r'<podcast:guid>[^<]+</podcast:guid>')
    keyless = guid_re.search(_serve(key=None))
    keyed = guid_re.search(_serve(key=KEY))
    assert keyless and keyed
    assert keyless.group(0) == keyed.group(0)  # feed identity never changes


def test_extract_cached_feed_auth_key_roundtrip():
    assert extract_cached_feed_auth_key(_serve(key=KEY)) == KEY
    assert extract_cached_feed_auth_key(_serve(key=None)) is None


def test_extract_key_falls_back_to_cover_token_for_episode_less_feeds():
    # A feed with zero enclosures still reports its key state via the badged
    # cover path token, so serve_rss's self-heal covers episode-less feeds.
    cover_only = (f'<rss><channel><image><url>{BASE}/authfeed/'
                  f'cover-minuspod-{VERSION}-{KEY}.jpg</url></image>'
                  f'</channel></rss>')
    assert extract_cached_feed_auth_key(cover_only) == KEY
    no_key_cover = (f'<rss><channel><image><url>{BASE}/authfeed/'
                    f'cover-minuspod-{VERSION}.jpg</url></image>'
                    f'</channel></rss>')
    assert extract_cached_feed_auth_key(no_key_cover) is None
