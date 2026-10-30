import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from tests.app_bootstrap import bootstrap

bootstrap('youtube_feed_refresh_test_')

import main_app.feeds as feeds_mod
from youtube import discovery
from youtube.feed import rebuild_youtube_feed


def _recent(hours=1):
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()


def _episode(video_id, published=None, title=None):
    return {'id': video_id[:12].ljust(12, '0'), 'video_id': video_id, 'url': f'https://www.youtube.com/watch?v={video_id}',
            'title': title or f'Video {video_id}', 'published': published or _recent(), 'description': '',
            'artwork_url': None, 'episode_number': None, 'rss_duration': 1800.0, 'upstream_chapters_url': None,
            'upstream_transcript_url': None, 'upstream_transcript_type': None, 'tags': []}


def _make_feed(slug, source_id='PL1', **extra):
    db = feeds_mod.db
    db.create_podcast(slug, f'youtube://playlist/{source_id}', title='YT', feed_type='youtube')
    db.update_podcast(slug, youtube_source_kind='playlist', youtube_source_id=source_id, **extra)
    db.set_setting('youtube_enabled', 'true', is_default=False)
    db.set_setting('youtube_process_delay_minutes', '15', is_default=False)


def _refresh(slug, episodes, source_id='PL1'):
    with patch.object(discovery, 'list_videos', return_value=episodes):
        return feeds_mod.refresh_rss_feed(slug, f'youtube://playlist/{source_id}', force=True)


def test_youtube_refresh_discovers_and_defers_to_sponsorblock_watch():
    db = feeds_mod.db
    slug = 'yt-refresh'
    _make_feed(slug)
    try:
        with patch.object(discovery, 'list_videos', return_value=[_episode('vid000000001')]), \
                patch.object(feeds_mod, 'rebuild_youtube_feed', return_value=True) as rebuild:
            outcome = feeds_mod.refresh_rss_feed(slug, 'youtube://playlist/PL1', force=True)
        assert outcome.success and outcome.status == 'refreshed'
        ep = db.get_episode(slug, 'vid000000001')
        assert ep['status'] == 'discovered'
        assert ep['youtube_video_id'] == 'vid000000001'
        assert ep['sb_state'] == 'waiting'
        assert ep['sb_next_check_at'] is not None
        assert not any(q['episode_id'] == 'vid000000001' for q in db.get_pending_queued_episodes())
        rebuild.assert_called_once()
    finally:
        db.delete_podcast(slug)


def test_second_refresh_does_not_rearm_the_wait():
    db = feeds_mod.db
    slug = 'yt-rearm'
    _make_feed(slug)
    eps = [_episode('vid000000001'), _episode('vid000000002')]
    try:
        _refresh(slug, eps)
        db.update_episode_sponsorblock(slug, eps[1]['id'], sb_state='probing', sb_attempts=2)
        first = db.get_episode(slug, eps[0]['id'])
        _refresh(slug, eps)
        again = db.get_episode(slug, eps[0]['id'])
        assert again['sb_next_check_at'] == first['sb_next_check_at']
        assert again['sb_attempts'] == first['sb_attempts']
        probing = db.get_episode(slug, eps[1]['id'])
        assert probing['sb_state'] == 'probing' and probing['sb_attempts'] == 2
    finally:
        db.delete_podcast(slug)


def test_out_of_window_video_is_not_scheduled():
    db = feeds_mod.db
    slug = 'yt-old'
    _make_feed(slug)
    try:
        ep = _episode('vid000000003', published=_recent(hours=24 * 365))
        _refresh(slug, [ep])
        assert db.get_episode(slug, ep['id'])['sb_state'] is None
    finally:
        db.delete_podcast(slug)


def test_title_skip_pattern_video_is_not_scheduled():
    db = feeds_mod.db
    slug = 'yt-skip'
    _make_feed(slug, title_skip_patterns=json.dumps(['trailer*']))
    try:
        ep = _episode('vid000000004', title='Trailer for next week')
        _refresh(slug, [ep])
        assert db.get_episode(slug, ep['id'])['sb_state'] is None
    finally:
        db.delete_podcast(slug)


def test_served_feed_lists_only_processed_episodes():
    db = feeds_mod.db
    slug = 'yt-served'
    _make_feed(slug)
    try:
        ep = {**_episode('vid000000005'), 'id': 'a1b2c3d4e5f6'}
        outcome = _refresh(slug, [ep])
        assert outcome.success
        assert '<item>' not in feeds_mod.storage.get_rss(slug)
        db.upsert_episode(slug, ep['id'], status='processed', processed_file=f'{slug}/{ep["id"]}.mp3')
        assert rebuild_youtube_feed(slug)
        xml = feeds_mod.storage.get_rss(slug)
        assert xml.count('<item>') == 1
        assert 'https://www.youtube.com/playlist?list=PL1' in xml
    finally:
        db.delete_podcast(slug)


def test_youtube_listing_failure_counts_as_refresh_failure():
    db = feeds_mod.db
    slug = 'yt-broken'
    _make_feed(slug, source_id='PL2')
    try:
        with patch.object(discovery, 'list_videos', side_effect=discovery.SourceNotFound('Playlist not found or private')):
            outcome = feeds_mod.refresh_rss_feed(slug, 'youtube://playlist/PL2', force=True)
        assert not outcome.success and outcome.status == 'fetch_failed'
        row = db.get_podcast_by_slug(slug)
        assert row['refresh_failure_count'] == 1
        assert 'not found' in row['last_refresh_error']
    finally:
        db.delete_podcast(slug)


def test_unexpected_error_returns_internal_error_and_clears_coalesce():
    db = feeds_mod.db
    slug = 'yt-boom'
    _make_feed(slug)
    try:
        with patch.object(discovery, 'list_videos', side_effect=RuntimeError('db locked')):
            outcome = feeds_mod.refresh_rss_feed(slug, 'youtube://playlist/PL1', force=True)
        assert not outcome.success and outcome.status == 'internal_error'
        assert feeds_mod._refresh_coalesce.get(slug) is None
        assert db.get_podcast_by_slug(slug)['refresh_failure_count'] in (0, None)
    finally:
        db.delete_podcast(slug)
