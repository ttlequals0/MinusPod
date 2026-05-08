"""Tests for the unified /all RSS feed.

Covers:
- DB query: get_recent_processed_across_all_feeds returns only processed
  episodes, sorted by published_at desc, joined with podcast metadata.
- XML builder: RSSParser.build_combined_feed produces valid RSS 2.0,
  prefixes episode titles with podcast title, builds the right enclosure
  URL shape, and degrades gracefully on an empty input.
"""

import os
import re
import sys

import defusedxml
defusedxml.defuse_stdlib()
from defusedxml import ElementTree as ET

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from rss_parser import RSSParser


class TestRecentProcessedAcrossAllFeeds:
    def _seed(self, db):
        db.create_podcast('alpha', 'https://a.example/feed.xml', 'Alpha Show')
        db.create_podcast('beta', 'https://b.example/feed.xml', 'Beta Show')
        # 3 processed episodes (one per feed + one extra), 1 pending — pending
        # must be excluded.
        db.upsert_episode(
            'alpha', 'a1',
            original_url='https://a.example/a1.mp3',
            title='Alpha Episode 1',
            status='processed',
            processed_file='episodes/a1.mp3',
            new_duration=1800,
            published_at='2026-05-01T10:00:00Z',
        )
        db.upsert_episode(
            'alpha', 'a2',
            original_url='https://a.example/a2.mp3',
            title='Alpha Episode 2',
            status='processed',
            processed_file='episodes/a2.mp3',
            new_duration=1900,
            published_at='2026-05-07T10:00:00Z',
        )
        db.upsert_episode(
            'beta', 'b1',
            original_url='https://b.example/b1.mp3',
            title='Beta Episode 1',
            status='processed',
            processed_file='episodes/b1.mp3',
            new_duration=2000,
            published_at='2026-05-05T10:00:00Z',
        )
        db.upsert_episode(
            'beta', 'b-pending',
            original_url='https://b.example/bp.mp3',
            title='Beta Pending',
            status='pending',
            published_at='2026-05-06T10:00:00Z',
        )

    def test_returns_only_processed_sorted_desc(self, temp_db):
        self._seed(temp_db)

        rows = temp_db.get_recent_processed_across_all_feeds(limit=10)

        assert [r['episode_id'] for r in rows] == ['a2', 'b1', 'a1']
        # Pending excluded
        assert all(r.get('episode_id') != 'b-pending' for r in rows)
        # Joined fields present
        assert rows[0]['podcast_slug'] == 'alpha'
        assert rows[0]['podcast_title'] == 'Alpha Show'

    def test_limit_caps_results(self, temp_db):
        self._seed(temp_db)

        rows = temp_db.get_recent_processed_across_all_feeds(limit=2)

        assert len(rows) == 2
        assert [r['episode_id'] for r in rows] == ['a2', 'b1']

    def test_zero_or_negative_limit_returns_empty(self, temp_db):
        self._seed(temp_db)

        assert temp_db.get_recent_processed_across_all_feeds(limit=0) == []
        assert temp_db.get_recent_processed_across_all_feeds(limit=-5) == []


class TestBuildCombinedFeed:
    def _episode(self, **overrides):
        base = {
            'episode_id': 'a2',
            'title': 'Alpha Episode 2',
            'description': 'Latest alpha',
            'published_at': '2026-05-07T10:00:00Z',
            'new_duration': 1900,
            'episode_number': 2,
            'processed_version': 0,
            'episode_artwork_url': None,
            'podcast_slug': 'alpha',
            'podcast_title': 'Alpha Show',
            'podcast_artwork_url': 'https://a.example/art.png',
        }
        base.update(overrides)
        return base

    def test_renders_well_formed_rss_with_n_items(self):
        parser = RSSParser(base_url='http://10.0.0.190:8080')
        episodes = [
            self._episode(),
            self._episode(
                episode_id='b1', title='Beta Episode 1',
                podcast_slug='beta', podcast_title='Beta Show',
                published_at='2026-05-05T10:00:00Z',
            ),
        ]

        xml = parser.build_combined_feed(episodes)

        root = ET.fromstring(xml)
        assert root.tag == 'rss'
        items = root.findall('./channel/item')
        assert len(items) == 2
        # Channel-level title is the unified label
        assert root.find('./channel/title').text == 'MinusPod — All Podcasts'

    def test_prefixes_podcast_title_in_episode_title(self):
        parser = RSSParser(base_url='http://10.0.0.190:8080')
        xml = parser.build_combined_feed([self._episode()])

        root = ET.fromstring(xml)
        title = root.find('./channel/item/title').text
        assert title == '[Alpha Show] Alpha Episode 2'

    def test_enclosure_url_uses_per_episode_route_shape(self):
        parser = RSSParser(base_url='http://10.0.0.190:8080')
        xml = parser.build_combined_feed([self._episode()])

        root = ET.fromstring(xml)
        encl = root.find('./channel/item/enclosure')
        assert encl.attrib['url'] == 'http://10.0.0.190:8080/episodes/alpha/a2.mp3'
        assert encl.attrib['type'] == 'audio/mpeg'

    def test_versioned_processed_file_url(self):
        parser = RSSParser(base_url='http://10.0.0.190:8080')
        xml = parser.build_combined_feed(
            [self._episode(processed_version=2)])

        encl = ET.fromstring(xml).find('./channel/item/enclosure')
        assert encl.attrib['url'] == 'http://10.0.0.190:8080/episodes/alpha/a2-v2.mp3'

    def test_guid_is_namespaced_by_slug_to_avoid_collision(self):
        parser = RSSParser(base_url='http://10.0.0.190:8080')
        # Two podcasts that happen to share an episode_id
        eps = [
            self._episode(episode_id='shared'),
            self._episode(
                episode_id='shared', title='Beta Shared',
                podcast_slug='beta', podcast_title='Beta Show',
            ),
        ]
        xml = parser.build_combined_feed(eps)

        guids = [g.text for g in ET.fromstring(xml).findall('./channel/item/guid')]
        assert guids == ['alpha::shared', 'beta::shared']

    def test_empty_episode_list_returns_valid_empty_channel(self):
        parser = RSSParser(base_url='http://10.0.0.190:8080')
        xml = parser.build_combined_feed([])

        root = ET.fromstring(xml)
        assert root.find('./channel/title').text == 'MinusPod — All Podcasts'
        assert root.findall('./channel/item') == []

    def test_channel_artwork_points_at_minuspod_logo(self):
        parser = RSSParser(base_url='http://10.0.0.190:8080')
        xml = parser.build_combined_feed([self._episode()])

        root = ET.fromstring(xml)
        image_url = root.find('./channel/image/url').text
        assert image_url == 'http://10.0.0.190:8080/ui/feed-icon.png'
        # itunes:image with the same href (Apple Podcasts requires it)
        ns = {'itunes': 'http://www.itunes.com/dtds/podcast-1.0.dtd'}
        itunes_image = root.find('./channel/itunes:image', ns)
        assert itunes_image is not None
        assert itunes_image.attrib['href'] == 'http://10.0.0.190:8080/ui/feed-icon.png'

    def test_skips_rows_missing_required_keys(self):
        parser = RSSParser(base_url='http://10.0.0.190:8080')
        # Missing podcast_slug — must be silently dropped, not raise.
        eps = [
            self._episode(),
            self._episode(podcast_slug=None, episode_id='orphan'),
        ]
        xml = parser.build_combined_feed(eps)
        items = ET.fromstring(xml).findall('./channel/item')
        assert len(items) == 1
