"""Tests for per-run processing stats capture (#519).

Covers the history-row stats blob, the per-episode run accessor, the
low-ad-yield baseline query, and RSS itunes:duration capture.
"""
import json

import pytest

from rss_parser import RSSParser


class TestHistoryStatsBlob:
    def test_stats_json_persists_and_reads_back(self, temp_db):
        temp_db.create_podcast('stats-show', 'https://example.com/f.xml', 'Stats Show')
        podcast = temp_db.get_podcast_by_slug('stats-show')
        stats = {'downloaded_duration': 3305.7, 'windows': {'total': 7, 'failed': 0}}

        temp_db.record_processing_history(
            podcast_id=podcast['id'], podcast_slug='stats-show',
            podcast_title='Stats Show', episode_id='ep1', episode_title='One',
            status='completed', ads_detected=6,
            processing_stats=stats,
        )
        temp_db.record_processing_history(
            podcast_id=podcast['id'], podcast_slug='stats-show',
            podcast_title='Stats Show', episode_id='ep1', episode_title='One',
            status='completed', ads_detected=1,
        )

        runs = temp_db.get_episode_processing_runs(podcast['id'], 'ep1')
        assert len(runs) == 2
        assert runs[0]['reprocess_number'] == 1
        assert json.loads(runs[0]['processing_stats_json']) == stats
        assert runs[1]['processing_stats_json'] is None

    def test_runs_scoped_to_episode(self, temp_db):
        temp_db.create_podcast('scope-show', 'https://example.com/f.xml', 'Scope')
        podcast = temp_db.get_podcast_by_slug('scope-show')
        temp_db.record_processing_history(
            podcast_id=podcast['id'], podcast_slug='scope-show',
            podcast_title='Scope', episode_id='ep-a', episode_title='A',
            status='completed')
        temp_db.record_processing_history(
            podcast_id=podcast['id'], podcast_slug='scope-show',
            podcast_title='Scope', episode_id='ep-b', episode_title='B',
            status='failed', error_message='boom')

        runs = temp_db.get_episode_processing_runs(podcast['id'], 'ep-a')
        assert [r['episode_id'] for r in runs] == ['ep-a']


class TestRecentAdYields:
    def _seed_processed(self, db, slug, ep_id, original, new):
        db.upsert_episode(slug, ep_id,
                          original_url=f'https://example.com/{ep_id}.mp3',
                          title=ep_id, status='processed',
                          original_duration=original, new_duration=new,
                          processed_at='2026-07-01T00:00:00Z')

    def test_returns_removed_seconds_excluding_target(self, temp_db):
        temp_db.create_podcast('yield-show', 'https://example.com/f.xml', 'Yield')
        podcast = temp_db.get_podcast_by_slug('yield-show')
        for i, removed in enumerate((600, 500, 550)):
            self._seed_processed(temp_db, 'yield-show', f'ep{i}',
                                 3300, 3300 - removed)
        self._seed_processed(temp_db, 'yield-show', 'target', 3300, 3270)
        # Non-processed rows must not count.
        temp_db.upsert_episode('yield-show', 'ep-disc',
                               original_url='https://example.com/d.mp3',
                               title='disc', status='discovered')

        yields = temp_db.get_recent_ad_yields(podcast['id'], 'target')
        assert sorted(yields) == [500, 550, 600]


class TestItunesDurationParse:
    @pytest.mark.parametrize('raw,expected', [
        ('3305', 3305.0),
        ('55:05', 3305.0),
        ('1:02:05', 3725.0),
        ('00:30', 30.0),
        ('', None),
        (None, None),
        ('abc', None),
        ('1:2:3:4', None),
        ('0', None),
    ])
    def test_forms(self, raw, expected):
        assert RSSParser._parse_itunes_duration(raw) == expected

    def test_extract_episodes_captures_rss_duration(self):
        feed = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
  <channel>
    <title>Duration Show</title>
    <item>
      <title>Ep One</title>
      <guid>ep-one</guid>
      <itunes:duration>55:05</itunes:duration>
      <enclosure url="https://example.com/one.mp3" type="audio/mpeg"/>
    </item>
    <item>
      <title>Ep Two</title>
      <guid>ep-two</guid>
      <enclosure url="https://example.com/two.mp3" type="audio/mpeg"/>
    </item>
  </channel>
</rss>"""
        episodes = RSSParser().extract_episodes(feed)
        by_title = {e['title']: e for e in episodes}
        assert by_title['Ep One']['rss_duration'] == 3305.0
        assert by_title['Ep Two']['rss_duration'] is None
