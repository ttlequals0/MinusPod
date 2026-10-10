"""Tests for the episode title blacklist: matcher, and all three enforcement
gates (RSS refresh queueing, on-demand JIT serve, worker claim) plus the
served-RSS hide-mode filter.
"""
import json
from unittest.mock import MagicMock, patch

import pytest

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('title_blacklist_test_')

from config import (
    title_matches_skip_patterns, description_matches_skip_patterns,
    duration_outside_feed_range,
)
from api.episodes import _episode_base_json
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

    def test_episode_summary_reports_title_skip(self):
        episode = {
            'episode_id': 'episode-1',
            'title': 'Weekly Sponsor Update',
            'status': 'discovered',
            'created_at': '2026-09-12T00:00:00Z',
            'processed_at': None,
            'original_duration': None,
            'new_duration': None,
            'ads_removed': 0,
        }
        assert _episode_base_json(
            episode,
            title_skip_patterns=json.dumps(['weekly sponsor*']),
        )['titleSkipped']


class TestDescriptionMatchesSkipPatterns:
    def test_glob_star_matches(self):
        assert description_matches_skip_patterns(
            'This is a preview. To hear the entire episode, subscribe.',
            json.dumps(['*This is a preview. To hear the entire episode*']))

    def test_case_insensitive(self):
        assert description_matches_skip_patterns(
            'THIS IS A PREVIEW', json.dumps(['*this is a preview*']))

    def test_substring_needs_stars(self):
        assert not description_matches_skip_patterns(
            'A preview of the show', json.dumps(['preview']))
        assert description_matches_skip_patterns(
            'A preview of the show', json.dumps(['*preview*']))

    def test_strips_html_before_matching(self):
        assert description_matches_skip_patterns(
            '<p>This is a <b>preview</b>.</p>',
            json.dumps(['*This is a preview.*']))

    def test_collapses_whitespace_before_matching(self):
        assert description_matches_skip_patterns(
            'This is a\n\npreview.', json.dumps(['*This is a preview.*']))

    def test_no_match_returns_false(self):
        assert not description_matches_skip_patterns(
            'Full episode notes', json.dumps(['*preview*']))

    def test_invalid_json_is_safe(self):
        assert not description_matches_skip_patterns('Episode notes', 'not-json')

    def test_empty_list_returns_false(self):
        assert not description_matches_skip_patterns('Episode notes', json.dumps([]))

    def test_none_patterns_returns_false(self):
        assert not description_matches_skip_patterns('Episode notes', None)

    def test_none_description_returns_false(self):
        assert not description_matches_skip_patterns(None, json.dumps(['*']))

    def test_episode_summary_reports_description_skip(self):
        episode = {
            'episode_id': 'episode-1',
            'title': 'Episode One',
            'description': 'This is a preview. To hear the entire episode, subscribe.',
            'status': 'discovered',
            'created_at': '2026-09-12T00:00:00Z',
            'processed_at': None,
            'original_duration': None,
            'new_duration': None,
            'ads_removed': 0,
        }
        assert _episode_base_json(
            episode,
            description_skip_patterns=json.dumps(['*this is a preview*']),
        )['descriptionSkipped']


class TestRssGateSkipsBlacklistedTitles:
    @pytest.mark.parametrize('duration_filters', [False, True])
    @patch('main_app.feeds._build_and_save_served_rss')
    @patch('main_app.feeds.pattern_service')
    @patch('main_app.feeds.status_service')
    @patch('main_app.feeds.storage')
    @patch('main_app.feeds.rss_parser')
    @patch('main_app.feeds.db')
    def test_matching_title_skipped_non_matching_queued(
            self, mock_db, mock_rss, mock_storage, mock_status,
            mock_pattern, _build_rss, duration_filters):
        from datetime import datetime, timezone
        from email.utils import format_datetime

        recent = format_datetime(datetime.now(timezone.utc))

        mock_db.get_podcast_by_slug.return_value = {
            'id': 1, 'etag': None, 'last_modified_header': None,
            'artwork_cached': True,
            'title_skip_patterns': None if duration_filters else json.dumps(['Blacklisted*']),
            'min_duration_seconds': 60 if duration_filters else None,
        }
        mock_db.get_podcast_row.return_value = mock_db.get_podcast_by_slug.return_value
        mock_db.bulk_upsert_discovered_episodes.return_value = (2, {}, {})
        mock_db.is_auto_process_enabled_for_podcast.return_value = True
        mock_db.get_episode_statuses_for_podcast.return_value = ({}, {})
        mock_db.queue_episodes_for_processing.return_value = {'ep-normal'}
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
             'title': 'Blacklisted Episode', 'description': '', 'published': recent, 'rss_duration': 30},
            {'id': 'ep-normal', 'url': 'https://e.test/b.mp3',
             'title': 'Normal Episode', 'description': '', 'published': recent, 'rss_duration': 120},
        ]

        feeds_mod.refresh_rss_feed('show', 'https://example.com/f.xml', force=True)

        queued = mock_db.queue_episodes_for_processing.call_args.args[1]
        assert [episode['episode_id'] for episode in queued] == ['ep-normal']


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

    @patch('main_app.processing.start_background_processing')
    @patch('main_app.routes._lookup_episode', return_value=LOOKUP)
    @patch('main_app.routes.status_service')
    @patch('main_app.routes.db')
    @patch('main_app.routes.get_feed_map',
           return_value={SLUG: {'in': 'https://example.com/f.xml', 'out': SLUG}})
    def test_matching_description_serves_original_without_processing(
            self, _feed_map, mock_db, _status, _lookup, mock_start, client):
        mock_db.get_episode.return_value = {
            'episode_id': self.EP, 'status': 'discovered',
            'original_url': 'https://example.com/ep.mp3',
        }
        mock_db.get_podcast_title_skip_patterns.return_value = None
        mock_db.get_podcast_description_skip_patterns.return_value = json.dumps(['desc'])

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
    def _run(self, reprocess_requested_at, start_return=(False, 'busy'),
             podcast_filters=None, rss_duration=None):
        queue_row = {
            'id': 7, 'podcast_slug': 'example-podcast',
            'episode_id': 'a1b2c3d4e5f6', 'original_url': 'https://e.test/a.mp3',
            'title': 'Blacklisted Episode', 'podcast_title': 'Example Podcast',
            'published_at': None, 'description': None,
        }
        mock_db = MagicMock()
        mock_db.claim_next_queued_episode.return_value = queue_row
        mock_db.get_podcast_by_slug.return_value = {
            **({'title_skip_patterns': json.dumps(['Blacklisted*'])}
               if podcast_filters is None else podcast_filters)}
        mock_db.is_auto_process_enabled_for_podcast.return_value = True
        mock_db.get_episode.return_value = {
            'reprocess_requested_at': reprocess_requested_at, 'rss_duration': rss_duration}
        # Maintenance runs on the dispatcher's first pass; give it a
        # well-formed result so it does not raise before the claim gate runs.
        mock_db.reset_orphaned_queue_items.return_value = (0, 0)
        mock_db.reset_failed_queue_items.return_value = 0

        from whisper_pool import WhisperPool
        inactive_pool = WhisperPool(lambda: {
            'enabled': False, 'backend': 'local', 'max_requests': 4, 'max_episodes': 1})

        with patch.object(background, 'db', mock_db), \
             patch.object(background, 'shutdown_event') as ev, \
             patch.object(background, 'get_pool', lambda: inactive_pool), \
             patch.object(background, 'reset_stuck_processing_episodes'), \
             patch('offline_queue.offline_queue_tick'), \
             patch('main_app.processing.start_background_processing',
                   return_value=start_return) as start:
            # A gate-skipped claim never adds to `running`, so the inner
            # claim loop only stops on is_set(); use an open-ended counter
            # rather than a fixed-length side_effect list.
            calls = {'n': 0}
            def _is_set():
                calls['n'] += 1
                return calls['n'] > 2
            ev.is_set.side_effect = _is_set
            ev.wait.return_value = None
            background.background_queue_processor()

        return mock_db, start

    def test_skips_without_reprocess_requested(self):
        mock_db, start = self._run(reprocess_requested_at=None)

        start.assert_not_called()
        statuses = [c.args for c in mock_db.close_claimed_queue_row.call_args_list]
        assert (7, 'completed', 'skipped: feed filters') in statuses

    def test_processes_when_reprocess_requested(self):
        mock_db, start = self._run(reprocess_requested_at='2026-08-07T00:00:00Z')

        start.assert_called_once()
        statuses = [c.args for c in mock_db.close_claimed_queue_row.call_args_list]
        assert (7, 'completed', 'skipped: feed filters') not in statuses


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

    def test_hide_description_patterns_excludes_matching_entries(self):
        parser = RSSParser(base_url="https://podsrv.example.test")
        items = "\n".join(f"""
            <item>
                <title>Episode {i}</title>
                <description>{desc}</description>
                <guid>guid-{i}</guid>
                <pubDate>Wed, 01 Jan 2025 00:00:00 +0000</pubDate>
                <enclosure url="https://cdn.example.com/{i}.mp3" type="audio/mpeg" length="100" />
            </item>
            """ for i, desc in enumerate(
                ['Normal show notes', 'This is a preview. Subscribe for more.']))
        feed_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
    <channel>
        <title>Test Podcast</title>
        <link>https://example.com</link>
        <description>For testing</description>
        {items}
    </channel>
</rss>
"""
        result = parser.modify_feed(
            feed_content, "test-pod",
            hide_description_patterns=json.dumps(['*This is a preview*']))

        assert 'Episode 0' in result
        assert 'Episode 1' not in result

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


@pytest.mark.parametrize(('duration', 'minimum', 'maximum', 'skipped'), [
    (None, 60, 180, False), (0, 60, 180, False),
    (float('nan'), 60, 180, False), (float('inf'), 60, 180, False),
    (59, 60, None, True), (60, 60, 180, False),
    (180, 60, 180, False), (181, None, 180, True),
])
def test_duration_filter_inclusive_bounds_and_unknown_lengths(duration, minimum, maximum, skipped):
    assert duration_outside_feed_range(duration, {
        'min_duration_seconds': minimum, 'max_duration_seconds': maximum,
    }) is skipped


@pytest.mark.parametrize('raw', ['bad', 'NaN', 'inf', '-10', '0', None])
def test_malformed_rss_duration_is_unknown(raw):
    assert RSSParser._parse_itunes_duration(raw) is None


def test_duration_filter_hides_upstream_and_appended_using_rss_duration():
    parser = RSSParser(base_url='https://example.com')
    content = _build_rss_with_titles(['Short', 'Kept', 'Unknown'])
    content = content.replace('<rss version="2.0">',
        '<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">')
    content = content.replace('<title>Short</title>', '<title>Short</title><itunes:duration>00:30</itunes:duration>')
    content = content.replace('<title>Kept</title>', '<title>Kept</title><itunes:duration>02:00</itunes:duration>')
    extras = [{
        'episode_id': 'extra', 'title': 'Long processed', 'description': '',
        'published_at': '2025-01-01T00:00:00Z', 'new_duration': 10,
        'rss_duration': 120, 'episode_number': None,
    }, {
        'episode_id': 'extra-short', 'title': 'Short processed', 'description': '',
        'published_at': '2025-01-01T00:00:00Z', 'new_duration': 10,
        'rss_duration': 30, 'episode_number': None,
    }]
    result = parser.modify_feed(content, 'example-podcast', extra_episodes=extras,
                                hide_min_duration_seconds=60)
    assert '<title>Short</title>' not in result
    assert 'Short processed' not in result
    assert '<title>Kept</title>' in result
    assert '<title>Unknown</title>' in result
    assert 'Long processed' in result


@pytest.mark.parametrize('manual', [False, True])
def test_queued_duration_filter_preserves_manual_reprocess_override(manual):
    db, start = TestClaimGateTitleBlacklist()._run(
        '2026-10-10T00:00:00Z' if manual else None,
        podcast_filters={'min_duration_seconds': 60}, rss_duration=30)
    assert start.call_count == int(manual)
    if not manual:
        db.close_claimed_queue_row.assert_any_call(7, 'completed', 'skipped: feed filters')


@pytest.mark.parametrize('duration', [30, 60, None])
def test_jit_duration_filter_uses_rss_metadata_before_processing(duration):
    slug, episode_id = 'example-podcast', 'a1b2c3d4e5f6'
    lookup = ({
        'id': episode_id, 'url': 'https://example.com/episode.mp3',
        'title': 'Episode', 'description': '', 'artwork_url': None,
        'rss_duration': duration,
    }, 'Example Podcast')
    with app.test_client() as client, \
            patch('main_app.routes.get_feed_map', return_value={slug: {'in': 'https://example.com/rss', 'out': slug}}), \
            patch('main_app.routes.db') as db, \
            patch('main_app.routes._lookup_episode', return_value=lookup), \
            patch('main_app.processing.start_background_processing', return_value=(True, None)) as start:
        db.get_episode.return_value = {
            'episode_id': episode_id, 'status': 'discovered',
            'original_url': 'https://example.com/episode.mp3',
        }
        db.get_podcast_by_slug.return_value = {
            'feed_type': 'subscribed', 'min_duration_seconds': 60}
        db.get_podcast_title_skip_patterns.return_value = None
        response = client.get(f'/episodes/{slug}/{episode_id}.mp3')
        if duration == 30:
            assert response.status_code == 302
            assert response.headers['Location'] == lookup[0]['url']
            start.assert_not_called()
        else:
            start.assert_called_once()
