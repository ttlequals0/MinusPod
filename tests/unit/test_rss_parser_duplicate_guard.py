"""Render-time duplicate guard in modify_feed: an upstream GUID rotation can
leave a discovery-layer duplicate in the DB (see episodes.py's fuzzy match
and the orphan cleanup migration). As a last line of defense, modify_feed
must never serve both the upstream item and the DB-appended item for the
same episode by title and date; the processed DB item wins.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from rss_parser import RSSParser

ITUNES_NS = 'http://www.itunes.com/dtds/podcast-1.0.dtd'


class FakeStorage:
    """Just enough surface for modify_feed's storage probes."""

    def has_artwork(self, slug):
        return False

    def has_transcript_vtt(self, slug, episode_id):
        return True

    def has_chapters_json(self, slug, episode_id):
        return True


def _feed_xml(guid='rotated-guid-123', pub_date='Tue, 30 Jun 2026 20:00:00 GMT',
              title='Episode One'):
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="{ITUNES_NS}"
     xmlns:podcast="https://podcastindex.org/namespace/1.0">
  <channel>
    <title>Source Show</title>
    <link>https://example.com</link>
    <description>D</description>
    <language>en</language>
    <item>
      <title>{title}</title>
      <enclosure url="https://example.com/ep1-new.mp3" type="audio/mpeg"/>
      <guid>{guid}</guid>
      <pubDate>{pub_date}</pubDate>
    </item>
  </channel>
</rss>"""


def _extra_episode(published_at='2026-06-30T23:00:00Z', title='Episode One'):
    return [{
        'episode_id': 'completedabc123',
        'title': title,
        'description': 'Already processed',
        'published_at': published_at,
        'new_duration': 500,
        'episode_number': 1,
        'processed_version': 2,
    }]


def _serve(feed_xml=None, extra=None):
    return RSSParser(base_url='https://minuspod.example').modify_feed(
        feed_xml or _feed_xml(), 'dup-guard-feed',
        storage=FakeStorage(), extra_episodes=extra if extra is not None else _extra_episode())


def test_rotated_guid_duplicate_renders_only_the_processed_db_item():
    output = _serve()

    assert output.count('<item>') == 1
    assert 'completedabc123' in output
    assert 'rotated-guid-123' not in output
    assert '<podcast:transcript' in output
    assert '<podcast:chapters' in output


def test_a_genuinely_different_episode_is_not_suppressed():
    far_feed = _feed_xml(pub_date='Tue, 07 Jul 2026 20:00:00 GMT')
    output = _serve(feed_xml=far_feed)

    assert output.count('<item>') == 2
