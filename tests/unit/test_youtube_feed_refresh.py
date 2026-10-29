from unittest.mock import patch

from tests.app_bootstrap import bootstrap

bootstrap('youtube_feed_refresh_test_')

import main_app.feeds as feeds_mod
from youtube import discovery


def _episode(video_id, published='2026-10-28T10:00:00+00:00'):
    return {'id': video_id[:12].ljust(12, '0'), 'video_id': video_id, 'url': f'https://www.youtube.com/watch?v={video_id}',
            'title': f'Video {video_id}', 'published': published, 'description': '', 'artwork_url': None,
            'episode_number': None, 'rss_duration': 1800.0, 'upstream_chapters_url': None,
            'upstream_transcript_url': None, 'upstream_transcript_type': None, 'tags': []}


def test_youtube_refresh_discovers_and_defers_to_sponsorblock_watch():
    db = feeds_mod.db
    slug = 'yt-refresh'
    db.create_podcast(slug, 'youtube://playlist/PL1', title='YT', feed_type='youtube')
    db.update_podcast(slug, youtube_source_kind='playlist', youtube_source_id='PL1')
    db.set_setting('youtube_enabled', 'true', is_default=False)
    db.set_setting('youtube_process_delay_minutes', '15', is_default=False)
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
        assert db.get_queue_status()['pending'] == 0 or not any(
            q['episode_id'] == 'vid000000001' for q in db.get_pending_queued_episodes())
        rebuild.assert_called_once()
    finally:
        db.delete_podcast(slug)


def test_youtube_listing_failure_counts_as_refresh_failure():
    db = feeds_mod.db
    slug = 'yt-broken'
    db.create_podcast(slug, 'youtube://playlist/PL2', title='YT', feed_type='youtube')
    db.update_podcast(slug, youtube_source_kind='playlist', youtube_source_id='PL2')
    try:
        with patch.object(discovery, 'list_videos', side_effect=discovery.SourceNotFound('Playlist not found or private')):
            outcome = feeds_mod.refresh_rss_feed(slug, 'youtube://playlist/PL2', force=True)
        assert not outcome.success and outcome.status == 'fetch_failed'
        row = db.get_podcast_by_slug(slug)
        assert row['refresh_failure_count'] == 1
        assert 'not found' in row['last_refresh_error']
    finally:
        db.delete_podcast(slug)
