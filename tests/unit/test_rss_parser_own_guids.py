"""Per-feed GUID scheme in the served RSS (#598).

modify_feed(own_episode_guids=True) must emit the MinusPod episode id as
each item's <guid isPermaLink="false">, matching the DB-appended items;
off (the default) must pass upstream GUIDs through byte-identically.
"""
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from rss_parser import RSSParser

BASE = 'https://mp.example.com'


def _feed_xml():
    return """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
  <channel>
    <title>Source Show</title>
    <link>https://example.com</link>
    <description>D</description>
    <item>
      <title>Ep One</title>
      <enclosure url="https://example.com/ep1.mp3" type="audio/mpeg"/>
      <guid>upstream-guid-1</guid>
    </item>
    <item>
      <title>Ep Two (no guid)</title>
      <enclosure url="https://example.com/ep2.mp3" type="audio/mpeg"/>
    </item>
  </channel>
</rss>"""


def _serve(**kwargs):
    return RSSParser(base_url=BASE).modify_feed(_feed_xml(), 'guidfeed', **kwargs)


def _episode_ids(served):
    return re.findall(r'/episodes/guidfeed/([0-9a-f]{12})\.mp3', served)


def test_off_passes_upstream_guids_through_byte_identically():
    default = _serve()
    explicit_off = _serve(own_episode_guids=False)
    assert default == explicit_off
    assert '<guid>upstream-guid-1</guid>' in default
    # No upstream id: today's behavior falls back to the upstream episode URL.
    assert '<guid>https://example.com/ep2.mp3</guid>' in default


def test_on_emits_minuspod_episode_ids():
    served = _serve(own_episode_guids=True)
    ep_ids = _episode_ids(served)
    assert len(ep_ids) == 2
    for ep_id in ep_ids:
        assert f'<guid isPermaLink="false">{ep_id}</guid>' in served
    # feedparser also mirrors a linkless item's guid into <link>, so check
    # the guid tag specifically rather than the raw string.
    assert '<guid>upstream-guid-1</guid>' not in served
    assert '<guid>https://example.com/ep2.mp3</guid>' not in served
    assert served.count('<guid') == 2


def test_on_and_off_serve_the_same_episode_ids():
    # The flag changes only the <guid> lines; enclosure URLs (and therefore
    # the ids apps download by) stay identical either way.
    assert _episode_ids(_serve()) == _episode_ids(_serve(own_episode_guids=True))
