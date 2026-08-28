"""Tests for the episode title blacklist: matcher, and all three enforcement
gates (RSS refresh queueing, on-demand JIT serve, worker claim) plus the
served-RSS hide-mode filter.
"""
import json
from unittest.mock import MagicMock, patch

import pytest

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('title_blacklist_test_')

from config import title_matches_skip_patterns
import main_app.feeds as feeds_mod
from main_app import app, background
from rss_parser import RSSParser


class TestTitleMatchesSkipPatterns:
    def test_glob_star_matches(self):
        assert title_matches_skip_patterns(
            'Weekly Sponsor Update', json.dumps(['Weekly Sponsor*']))

    def test_case_insensitive(self):
        assert title_matches_skip_patterns(
            'WEEKLY SPONSOR UPDATE', json.dumps(['weekly sponsor*']))

    def test_substring_needs_stars(self):
        # A plain substring pattern only matches the whole title, not part of it.
        assert not title_matches_skip_patterns(
            'A Weekly Sponsor Update', json.dumps(['Weekly Sponsor']))
        assert title_matches_skip_patterns(
            'A Weekly Sponsor Update', json.dumps(['*Weekly Sponsor*']))

    def test_no_match_returns_false(self):
        assert not title_matches_skip_patterns(
            'Episode One', json.dumps(['Weekly Sponsor*']))

    def test_invalid_json_is_safe(self):
        assert not title_matches_skip_patterns('Episode One', 'not-json')

    def test_empty_list_returns_false(self):
        assert not title_matches_skip_patterns('Episode One', json.dumps([]))

    def test_none_patterns_returns_false(self):
        assert not title_matches_skip_patterns('Episode One', None)

    def test_none_title_returns_false(self):
        assert not title_matches_skip_patterns(None, json.dumps(['*']))


class TestRssGateSkipsBlacklistedTitles:
    @patch('main_app.feeds._build_and_save_served_rss')
    @patch('main_app.feeds.pattern_service')
    @patch('main_app.feeds.status_service')
    @patch('main_app.feeds.storage')
    @patch('main_app.feeds.rss_parser')
    @patch('main_app.feeds.db')
    def test_matching_title_skipped_non_matching_queued(
            self, mock_db, mock_rss, mock_storage, mock_status,
            mock_pattern, _build_rss):
        from datetime import datetime, timezone
        from email.utils import format_datetime

        recent = format_datetime(datetime.now(timezone.utc))

        mock_db.get_podcast_by_slug.return_value = {
            'id': 1, 'etag': None, 'last_modified_header': None,
            'artwork_cached': True,
            'title_skip_patterns': json.dumps(['Blacklisted*']),
        }
        mock_db.bulk_upsert_discovered_episodes.return_value = 2
        mock_db.is_auto_process_enabled_for_podcast.return_value = True
        mock_db.get_episode_statuses_for_podcast.return_value = ({}, {})
        mock_db.queue_episode_for_processing.return_value = 99
        mock_pattern.update_podcast_metadata.return_value = {}

        mock_rss.fetch_feed_conditional.return_value = (b'<rss/>', None, None)
        parsed = MagicMock()
        parsed.feed = {'title': 'Show', 'description': '', 'link': ''}
        parsed.entries = [1, 2]
        parsed.bozo = False
        mock_rss.parse_feed.return_value = parsed
        mock_rss.find_channel_element.return_value = None
        mock_rss.resolve_channel_fields.return_value = {
            'title': 'Show', 'description': '', 'link': '', 'language': 'en',
            'author': '', 'categories': []}
        mock_rss.extract_podcast_artwork_url.return_value = None
        mock_rss.extract_podping_declaration.return_value = {
            'uses_podping': None, 'hive_accounts': []}
        mock_rss.extract_episodes.return_value = [
            {'id': 'ep-blacklisted', 'url': 'https://e.test/a.mp3',
             'title': 'Blacklisted Episode', 'description': '', 'published': recent},
            {'id': 'ep-normal', 'url': 'https://e.test/b.mp3',
             'title': 'Normal Episode', 'description': '', 'published': recent},
        ]

        feeds_mod.refresh_rss_feed('show', 'https://example.com/f.xml', force=True)

        queued_ids = [c.args[1] for c in mock_db.queue_episode_for_processing.call_args_list]
        assert queued_ids == ['ep-normal']


class TestOnDemandServeGate:
    EP = 'a1b2c3d4e5f6'
    SLUG = 'example-podcast'
    LOOKUP = ({'id': EP, 'url': 'https://example.com/ep.mp3', 'title': 'Blacklisted Episode',
               'description': 'desc', 'artwork_url': None,
               'published': '2026-07-22T04:12:25Z'}, 'Example Podcast')

    @pytest.fixture
    def client(self):
        app.config['TESTING'] = True
        with app.test_client() as c:
            yield c

    @patch('main_app.processing.start_background_processing')
    @patch('main_app.routes._lookup_episode', return_value=LOOKUP)
    @patch('main_app.routes.status_service')
    @patch('main_app.routes.db')
    @patch('main_app.routes.get_feed_map',
           return_value={SLUG: {'in': 'https://example.com/f.xml', 'out': SLUG}})
    def test_matching_title_serves_original_without_processing(
            self, _feed_map, mock_db, _status, _lookup, mock_start, client):
        mock_db.get_episode.return_value = {
            'episode_id': self.EP, 'status': 'discovered',
            'original_url': 'https://example.com/ep.mp3',
        }
        mock_db.get_podcast_title_skip_patterns.return_value = json.dumps(['Blacklisted*'])

        resp = client.get(f'/episodes/{self.SLUG}/{self.EP}.mp3')

        assert resp.status_code == 302
        assert resp.headers['Location'] == 'https://example.com/ep.mp3'
        mock_start.assert_not_called()

    LOCAL_EP = 's01e01'
    LOCAL_LOOKUP = ({'id': LOCAL_EP, 'url': 'local://s01e01', 'title': 'Blacklisted Episode',
                     'description': 'desc', 'artwork_url': None,
                     'published': '2026-07-22T04:12:25Z'}, 'Example Local Podcast')

    @patch('main_app.processing.start_background_processing')
    @patch('main_app.routes._lookup_episode', return_value=LOCAL_LOOKUP)
    @patch('main_app.routes.status_service')
    @patch('main_app.routes.db')
    @patch('main_app.routes.get_feed_map',
           return_value={SLUG: {'in': 'local://example-podcast', 'out': SLUG}})
    def test_local_feed_matching_title_does_not_redirect(
            self, _feed_map, mock_db, _status, _lookup, mock_start, client):
        """A local episode's original_url is the unreachable local:// sentinel,
        so a title-blacklist match must never 302 to it (#625 review): the
        blacklist is skipped entirely for local feeds and the episode
        processes normally instead."""
        mock_db.get_episode.return_value = {
            'episode_id': self.LOCAL_EP, 'status': 'discovered',
            'original_url': 'local://s01e01',
        }
        mock_db.get_podcast_by_slug.return_value = {
            'feed_type': 'local', 'title_skip_patterns': json.dumps(['Blacklisted*']),
        }
        mock_db.get_podcast_title_skip_patterns.return_value = json.dumps(['Blacklisted*'])
        mock_start.return_value = (True, None)

        resp = client.get(f'/episodes/{self.SLUG}/{self.LOCAL_EP}.mp3')

        assert resp.status_code != 302
        mock_start.assert_called_once()


class TestClaimGateTitleBlacklist:
    def _run(self, reprocess_requested_at, start_return=(False, 'busy')):
        queue_row = {
            'id': 7, 'podcast_slug': 'example-podcast',
            'episode_id': 'a1b2c3d4e5f6', 'original_url': 'https://e.test/a.mp3',
            'title': 'Blacklisted Episode', 'podcast_title': 'Example Podcast',
            'published_at': None, 'description': None,
        }
        mock_db = MagicMock()
        mock_db.claim_next_queued_episode.return_value = queue_row
        mock_db.get_podcast_by_slug.return_value = {
            'title_skip_patterns': json.dumps(['Blacklisted*'])}
        mock_db.is_auto_process_enabled_for_podcast.return_value = True
        mock_db.get_episode.return_value = {'reprocess_requested_at': reprocess_requested_at}

        with patch.object(background, 'db', mock_db), \
             patch.object(background, 'shutdown_event') as ev, \
             patch('main_app.processing.start_background_processing',
                   return_value=start_return) as start:
            ev.is_set.side_effect = [False, True]
            ev.wait.return_value = None
            background.background_queue_processor()

        return mock_db, start

    def test_skips_without_reprocess_requested(self):
        mock_db, start = self._run(reprocess_requested_at=None)

        start.assert_not_called()
        statuses = [c.args for c in mock_db.close_claimed_queue_row.call_args_list]
        assert (7, 'completed', 'skipped: title blacklist') in statuses

    def test_processes_when_reprocess_requested(self):
        mock_db, start = self._run(reprocess_requested_at='2026-08-07T00:00:00Z')

        start.assert_called_once()
        statuses = [c.args for c in mock_db.close_claimed_queue_row.call_args_list]
        assert (7, 'completed', 'skipped: title blacklist') not in statuses


def _build_rss_with_titles(titles):
    """Minimal RSS 2.0 feed with one item per title."""
    items = "\n".join(
        f"""
        <item>
            <title>{title}</title>
            <guid>guid-{i}</guid>
            <pubDate>Wed, 01 Jan 2025 00:00:00 +0000</pubDate>
            <enclosure url="https://cdn.example.com/{i}.mp3" type="audio/mpeg" length="100" />
        </item>
        """
        for i, title in enumerate(titles)
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
    <channel>
        <title>Test Podcast</title>
        <link>https://example.com</link>
        <description>For testing</description>
        {items}
    </channel>
</rss>
"""


class TestHideModeFiltersMatchingIds:
    def test_hide_title_patterns_excludes_matching_entries(self):
        parser = RSSParser(base_url="https://podsrv.example.test")
        feed_content = _build_rss_with_titles(
            ['Episode A', 'Blacklisted Episode', 'Episode C'])

        result = parser.modify_feed(
            feed_content, "test-pod",
            hide_title_patterns=json.dumps(['Blacklisted*']))

        assert 'Episode A' in result
        assert 'Episode C' in result
        assert 'Blacklisted Episode' not in result

    def test_no_hide_patterns_keeps_everything(self):
        parser = RSSParser(base_url="https://podsrv.example.test")
        feed_content = _build_rss_with_titles(['Episode A', 'Blacklisted Episode'])

        result = parser.modify_feed(feed_content, "test-pod", hide_title_patterns=None)

        assert 'Episode A' in result
        assert 'Blacklisted Episode' in result

    def test_hide_title_patterns_excludes_db_appended_extra_episodes(self):
        parser = RSSParser(base_url="https://podsrv.example.test")
        feed_content = _build_rss_with_titles(['Episode A'])
        extra_episodes = [
            {'episode_id': 'extra-1', 'title': 'Blacklisted Extra',
             'description': '', 'published_at': '2025-01-01T00:00:00Z',
             'new_duration': 100.0, 'episode_number': None},
            {'episode_id': 'extra-2', 'title': 'Kept Extra',
             'description': '', 'published_at': '2025-01-01T00:00:00Z',
             'new_duration': 100.0, 'episode_number': None},
        ]

        result = parser.modify_feed(
            feed_content, "test-pod", extra_episodes=extra_episodes,
            hide_title_patterns=json.dumps(['Blacklisted*']))

        assert 'Kept Extra' in result
        assert 'Blacklisted Extra' not in result
