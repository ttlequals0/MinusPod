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


def test_served_feed_lists_discovered_episode_unless_only_processed():
    db = feeds_mod.db
    slug = 'yt-discovered'
    _make_feed(slug)
    try:
        done = {**_episode('vid000000006'), 'id': 'a1b2c3d4e5f7'}
        new = {**_episode('vid000000007', published='2026-10-27T00:00:00Z'), 'id': 'b1b2c3d4e5f7'}
        assert _refresh(slug, [done, new]).success
        db.upsert_episode(slug, done['id'], status='processed', processed_file=f'{slug}/{done["id"]}.mp3')
        assert rebuild_youtube_feed(slug)
        xml = feeds_mod.storage.get_rss(slug)
        assert xml.count('<item>') == 2
        item = xml.split(f'<guid isPermaLink="false">{new["id"]}</guid>')[1].split('</item>')[0]
        assert f'/episodes/{slug}/{new["id"]}.mp3' in item and 'length=' not in item
        assert 'Tue, 27 Oct 2026' in item and '<itunes:duration>1800</itunes:duration>' in item
        assert 'podcast:transcript' not in item and 'podcast:chapters' not in item
        db.update_podcast(slug, only_expose_processed_episodes=1)
        assert rebuild_youtube_feed(slug)
        assert feeds_mod.storage.get_rss(slug).count('<item>') == 1
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


def _resolved(url='https://i.ytimg.com/src.jpg'):
    return discovery.SourceInfo('playlist', 'PL1', 'YT', '', url, 3)


def test_refresh_fetches_source_artwork_once_when_cache_missing():
    db = feeds_mod.db
    slug = 'yt-art-missing'
    _make_feed(slug)
    try:
        with patch.object(discovery, 'resolve_source', return_value=_resolved()) as resolve, \
                patch.object(feeds_mod.storage, 'download_artwork', return_value=True) as dl, \
                patch.object(feeds_mod, 'rebuild_youtube_feed', return_value=True):
            _refresh(slug, [_episode('vid000000001')])
        resolve.assert_called_once()
        dl.assert_called_once_with(slug, ['https://i.ytimg.com/src.jpg'], force=False)
    finally:
        db.delete_podcast(slug)


def test_refresh_skips_source_lookup_when_artwork_cached():
    db = feeds_mod.db
    slug = 'yt-art-cached'
    _make_feed(slug, artwork_cached=1)
    try:
        with patch.object(discovery, 'resolve_source') as resolve, \
                patch.object(feeds_mod.storage, 'download_artwork') as dl, \
                patch.object(feeds_mod, 'rebuild_youtube_feed', return_value=True):
            _refresh(slug, [_episode('vid000000001')])
        resolve.assert_not_called()
        dl.assert_not_called()
    finally:
        db.delete_podcast(slug)


def test_refresh_feed_artwork_forces_download_for_youtube_feed():
    db = feeds_mod.db
    slug = 'yt-art-manual'
    _make_feed(slug)
    try:
        with patch.object(discovery, 'resolve_source', return_value=_resolved()), \
                patch.object(feeds_mod.storage, 'download_artwork', return_value=True) as dl, \
                patch.object(feeds_mod, 'rebuild_youtube_feed', return_value=True):
            assert feeds_mod.refresh_feed_artwork(slug) is True
        dl.assert_called_once_with(slug, ['https://i.ytimg.com/src.jpg'], force=True)
    finally:
        db.delete_podcast(slug)


def test_youtube_feed_rss_rows_are_published_only():
    from local_feed_builder import _fetch_local_feed_episodes
    db = feeds_mod.db
    slug = 'yt-rss-rows'
    _make_feed(slug)
    try:
        pid = db.get_podcast_by_slug(slug)['id']
        db.upsert_episode(slug, 'done1', original_url='u', status='processed',
                          processed_file='x.mp3', published_at='2026-10-01T00:00:00Z')
        db.upsert_episode(slug, 'wait1', original_url='u', status='discovered')
        db.upsert_episode(slug, 'redo1', original_url='u', status='processing',
                          processed_file='y.mp3', published_at='2026-09-01T00:00:00Z')
        rows = _fetch_local_feed_episodes(db, pid, None, processed_only=True)
        assert [r['episode_id'] for r in rows] == ['done1', 'redo1']
    finally:
        db.delete_podcast(slug)


def test_existing_row_without_video_id_is_backfilled_by_refresh():
    db = feeds_mod.db
    slug = 'yt-backfill'
    _make_feed(slug)
    try:
        ep = _episode('vid000000002')
        db.upsert_episode(slug, ep['id'], original_url=ep['url'], status='discovered')
        assert db.get_episode(slug, ep['id'])['youtube_video_id'] is None
        _refresh(slug, [ep])
        assert db.get_episode(slug, ep['id'])['youtube_video_id'] == 'vid000000002'
    finally:
        db.delete_podcast(slug)


def test_disabled_master_switch_refresh_lists_nothing_and_rebuilds_from_db():
    db = feeds_mod.db
    slug = 'yt-off'
    _make_feed(slug)
    db.set_setting('youtube_enabled', 'false', is_default=False)
    try:
        with patch.object(discovery, 'list_videos', side_effect=AssertionError('listed')), \
                patch.object(discovery, 'resolve_source', side_effect=AssertionError('resolved')), \
                patch.object(feeds_mod, 'rebuild_youtube_feed', return_value=True) as rebuild:
            outcome = feeds_mod.refresh_rss_feed(slug, 'youtube://playlist/PL1', force=True)
            assert feeds_mod.refresh_feed_artwork(slug) is True
        assert outcome.success and outcome.status == 'disabled'
        assert rebuild.call_count == 2
    finally:
        db.delete_podcast(slug)


def test_lookup_episode_never_fetches_an_upstream_feed_for_youtube():
    from main_app import routes
    db = feeds_mod.db
    slug = 'yt-lookup'
    _make_feed(slug)
    try:
        db.upsert_episode(slug, 'a1b2c3d4e5f6', original_url='https://www.youtube.com/watch?v=vid1', title='T')
        with patch('main_app.routes.rss_parser.fetch_feed', side_effect=AssertionError('fetched')):
            ep, _name = routes._lookup_episode(slug, 'a1b2c3d4e5f6', {slug: {'in': 'youtube://playlist/PL1'}})
        assert ep['url'] == 'https://www.youtube.com/watch?v=vid1'
    finally:
        db.delete_podcast(slug)


def test_jit_returns_503_for_an_unprocessed_youtube_episode_when_disabled():
    from main_app import app
    db = feeds_mod.db
    slug = 'yt-jit-off'
    _make_feed(slug)
    db.set_setting('youtube_enabled', 'false', is_default=False)
    try:
        db.upsert_episode(slug, 'a1b2c3d4e5f6', original_url='https://www.youtube.com/watch?v=vid1',
                          title='T', status='discovered')
        feed_map = {slug: {'in': 'youtube://playlist/PL1', 'out': slug}}
        with patch('main_app.routes.get_feed_map', return_value=feed_map), \
                patch('main_app.processing.start_background_processing') as start:
            with app.test_client() as c:
                resp = c.get(f'/episodes/{slug}/a1b2c3d4e5f6.mp3')
        assert resp.status_code == 503
        assert b'YouTube feeds are disabled in Settings' in resp.data
        start.assert_not_called()
    finally:
        db.delete_podcast(slug)


def test_missing_source_artwork_is_not_looked_up_again_on_every_refresh():
    db = feeds_mod.db
    slug = 'yt-art-backoff'
    _make_feed(slug)
    feeds_mod._youtube_artwork_retry_at.pop(slug, None)
    try:
        info = _resolved(url=None)
        with patch.object(discovery, 'resolve_source', return_value=info) as resolve, \
                patch.object(feeds_mod, 'rebuild_youtube_feed', return_value=True):
            _refresh(slug, [_episode('vid000000001')])
            _refresh(slug, [_episode('vid000000001')])
        resolve.assert_called_once()
    finally:
        feeds_mod._youtube_artwork_retry_at.pop(slug, None)
        db.delete_podcast(slug)
