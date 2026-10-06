"""modify_feed's render-time guard against a leftover discovery-layer
duplicate: an upstream item must never be served alongside its own
DB-appended item for the same episode."""
import logging
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


def test_a_7_hour_drift_still_matches():
    output = _serve(
        feed_xml=_feed_xml(pub_date='Wed, 01 Jul 2026 03:00:00 GMT'),
        extra=_extra_episode(published_at='2026-06-30T20:00:00Z'))

    assert output.count('<item>') == 1


def test_a_24_hour_gap_is_not_suppressed():
    """A daily show's same-titled episode 24h later is not a dropped
    timezone offset and must render as its own item."""
    output = _serve(
        feed_xml=_feed_xml(pub_date='Wed, 01 Jul 2026 20:00:00 GMT'),
        extra=_extra_episode(published_at='2026-06-30T20:00:00Z'))

    assert output.count('<item>') == 2


def test_a_non_quarter_hour_drift_is_not_suppressed():
    output = _serve(
        feed_xml=_feed_xml(pub_date='Wed, 01 Jul 2026 03:05:00 GMT'),
        extra=_extra_episode(published_at='2026-06-30T20:00:00Z'))

    assert output.count('<item>') == 2


_TWO_ITEM_FEED = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="{ITUNES_NS}"
     xmlns:podcast="https://podcastindex.org/namespace/1.0">
  <channel>
    <title>Source Show</title>
    <link>https://example.com</link>
    <description>D</description>
    <language>en</language>
    <item>
      <title>Episode One</title>
      <enclosure url="https://example.com/ep1-new.mp3" type="audio/mpeg"/>
      <guid>same-episode-guid</guid>
      <pubDate>Tue, 30 Jun 2026 20:00:00 GMT</pubDate>
    </item>
    <item>
      <title>Episode Two</title>
      <enclosure url="https://example.com/ep2.mp3" type="audio/mpeg"/>
      <guid>ep2-guid</guid>
      <pubDate>Tue, 07 Jul 2026 20:00:00 GMT</pubDate>
    </item>
  </channel>
</rss>"""


def test_processed_episode_matching_its_own_upstream_guid_keeps_its_position(caplog):
    """extra_episodes includes processed episodes still listed upstream
    under their own GUID; those must render inline, not get suppressed as
    a self-duplicate and re-appended at the end."""
    parser = RSSParser(base_url='https://minuspod.example')
    episode_id = parser.generate_episode_id(
        'https://example.com/ep1-new.mp3', 'same-episode-guid')
    extra = [{
        'episode_id': episode_id,
        'title': 'Episode One',
        'description': 'Already processed',
        'published_at': '2026-06-30T20:00:00Z',
        'new_duration': 500,
        'episode_number': 1,
        'processed_version': 2,
    }]

    with caplog.at_level(logging.WARNING, logger='rss_parser'):
        output = parser.modify_feed(
            _TWO_ITEM_FEED, 'dup-guard-feed', storage=FakeStorage(), extra_episodes=extra)

    assert output.count('<item>') == 2
    assert output.index('Episode One') < output.index('Episode Two')
    assert not any('Suppressed' in r.message for r in caplog.records)
