"""unprocessed_playback: 'original' redirects unprocessed episodes upstream."""
import os
import tempfile
from unittest.mock import patch

import pytest

from tests.app_bootstrap import bootstrap

bootstrap('unprocessed_playback_test_', reset_storage=True)
from config import resolve_unprocessed_playback  # noqa: E402
from main_app import app, db  # noqa: E402

SLUG = 'unproc-playback'
EPISODE_ID = 'abc123def456'
EP = {'url': 'https://cdn.example.com/ep1.mp3', 'title': 'Ep 1',
      'description': '', 'artwork_url': None, 'published': None}
FEED_MAP = {SLUG: {'in': 'https://example.com/feed.xml', 'out': SLUG}}


@pytest.fixture(autouse=True)
def feed():
    db.create_podcast(SLUG, 'https://example.com/feed.xml', 'Unproc Test')
    db.set_setting('unprocessed_playback', 'process', is_default=False)
    yield
    db.delete_podcast(SLUG)
    db.set_setting('unprocessed_playback', 'process', is_default=False)


def _get(path_agent='Pocket Casts'):
    with patch('main_app.routes.get_feed_map', return_value=FEED_MAP), \
         patch('main_app.routes._lookup_episode', return_value=(EP, 'Show')), \
         patch('main_app.processing.start_background_processing',
               return_value=(True, None)) as start:
        with app.test_client() as c:
            resp = c.get(f'/episodes/{SLUG}/{EPISODE_ID}.mp3',
                         headers={'User-Agent': path_agent})
    return resp, start


def test_default_is_process_and_starts_jit():
    resp, start = _get()
    assert resp.status_code == 503
    start.assert_called_once()


def test_original_global_redirects_without_jit():
    db.set_setting('unprocessed_playback', 'original', is_default=False)
    resp, start = _get()
    assert resp.status_code == 302
    assert resp.headers['Location'] == EP['url']
    start.assert_not_called()


def test_original_redirects_while_processing_or_failed():
    db.set_setting('unprocessed_playback', 'original', is_default=False)
    for status in ('processing', 'failed'):
        db.upsert_episode(SLUG, EPISODE_ID, original_url=EP['url'],
                          title='Ep 1', status=status)
        resp, start = _get()
        assert resp.status_code == 302, status
        start.assert_not_called()


def test_original_serves_processed_audio():
    db.set_setting('unprocessed_playback', 'original', is_default=False)
    db.upsert_episode(SLUG, EPISODE_ID, original_url=EP['url'],
                      title='Ep 1', status='processed')
    with tempfile.NamedTemporaryFile(suffix='.mp3', delete=False) as f:
        f.write(b'ID3audio')
        path = f.name
    try:
        with patch('main_app.routes._published_processed_path', return_value=path):
            resp, start = _get()
        assert resp.status_code == 200
        assert resp.headers['Content-Type'].startswith('audio/mpeg')
        start.assert_not_called()
    finally:
        os.unlink(path)


def test_feed_override_wins_over_global():
    db.set_setting('unprocessed_playback', 'process', is_default=False)
    db.update_podcast(SLUG, unprocessed_playback='original')
    resp, start = _get()
    assert resp.status_code == 302
    start.assert_not_called()

    db.set_setting('unprocessed_playback', 'original', is_default=False)
    db.update_podcast(SLUG, unprocessed_playback='process')
    resp, start = _get()
    assert resp.status_code == 503
    start.assert_called_once()


def test_resolver_precedence_and_bad_values():
    db.set_setting('unprocessed_playback', 'original', is_default=False)
    assert resolve_unprocessed_playback(db, {}) == 'original'
    assert resolve_unprocessed_playback(db, {'unprocessed_playback': 'process'}) == 'process'
    assert resolve_unprocessed_playback(db, {'unprocessed_playback': 'bogus'}) == 'original'
    db.set_setting('unprocessed_playback', 'bogus', is_default=False)
    assert resolve_unprocessed_playback(db, None) == 'process'


def test_settings_api_roundtrip_and_validation():
    with app.test_client() as c:
        bad = c.put('/api/v1/settings/ad-detection', json={'unprocessedPlayback': 'nope'})
        assert bad.status_code == 400
        ok = c.put('/api/v1/settings/ad-detection', json={'unprocessedPlayback': 'original'})
        assert ok.status_code == 200
        got = c.get('/api/v1/settings').get_json()
        assert got['unprocessedPlayback']['value'] == 'original'
        feed_bad = c.patch(f'/api/v1/feeds/{SLUG}', json={'unprocessedPlayback': 'nope'})
        assert feed_bad.status_code == 400
        feed_ok = c.patch(f'/api/v1/feeds/{SLUG}', json={'unprocessedPlayback': 'process'})
        assert feed_ok.status_code == 200
        assert feed_ok.get_json()['unprocessedPlayback'] == 'process'


def test_awaiting_approval_episode_redirects_in_original_mode():
    db.set_setting('unprocessed_playback', 'original', is_default=False)
    db.upsert_episode(SLUG, EPISODE_ID, original_url=EP['url'],
                      title='Ep 1', status='processed')
    with db.get_connection() as conn:
        conn.execute("UPDATE episodes SET approval_pending_at='2026-11-05T10:00:00Z' "
                     "WHERE episode_id=?", (EPISODE_ID,))
    resp, start = _get()
    assert resp.status_code == 302
    assert resp.headers['Location'] == EP['url']
    start.assert_not_called()


def test_awaiting_approval_unchanged_in_process_mode():
    db.upsert_episode(SLUG, EPISODE_ID, original_url=EP['url'],
                      title='Ep 1', status='processed')
    with db.get_connection() as conn:
        conn.execute("UPDATE episodes SET approval_pending_at='2026-11-05T10:00:00Z' "
                     "WHERE episode_id=?", (EPISODE_ID,))
    resp, _ = _get()
    assert resp.status_code == 503


def test_processed_with_missing_file_redirects_in_original_mode():
    db.set_setting('unprocessed_playback', 'original', is_default=False)
    db.upsert_episode(SLUG, EPISODE_ID, original_url=EP['url'],
                      title='Ep 1', status='processed')
    with patch('main_app.routes._published_processed_path', return_value=None):
        resp, start = _get()
    assert resp.status_code == 302
    start.assert_not_called()


def test_unknown_episode_in_original_mode_is_404_and_never_processes():
    db.set_setting('unprocessed_playback', 'original', is_default=False)
    with patch('main_app.routes.get_feed_map', return_value=FEED_MAP), \
         patch('main_app.routes._lookup_episode', return_value=(None, None)), \
         patch('main_app.processing.start_background_processing') as start:
        with app.test_client() as c:
            resp = c.get(f'/episodes/{SLUG}/{EPISODE_ID}.mp3')
    assert resp.status_code == 404
    start.assert_not_called()


def test_head_in_original_mode_proxies_upstream_headers():
    db.set_setting('unprocessed_playback', 'original', is_default=False)
    from flask import Response
    with patch('main_app.routes.get_feed_map', return_value=FEED_MAP), \
         patch('main_app.routes._lookup_episode', return_value=(EP, 'Show')), \
         patch('main_app.routes._head_upstream',
               return_value=Response(status=200)) as head, \
         patch('main_app.processing.start_background_processing') as start:
        with app.test_client() as c:
            resp = c.head(f'/episodes/{SLUG}/{EPISODE_ID}.mp3')
    assert resp.status_code == 200
    head.assert_called_once_with(SLUG, EPISODE_ID, EP['url'])
    start.assert_not_called()


def test_failed_episode_redirects_without_touching_retry_state():
    """Playback in original mode never bumps FAILED -> PERMANENTLY_FAILED;
    retries stay with the auto-process queue."""
    db.set_setting('unprocessed_playback', 'original', is_default=False)
    db.upsert_episode(SLUG, EPISODE_ID, original_url=EP['url'], title='Ep 1',
                      status='failed')
    with db.get_connection() as conn:
        conn.execute("UPDATE episodes SET retry_count=99 WHERE episode_id=?", (EPISODE_ID,))
    resp, start = _get()
    assert resp.status_code == 302
    start.assert_not_called()
    assert db.get_episode(SLUG, EPISODE_ID)['status'] == 'failed'


def test_local_feed_ignores_original_mode():
    slug = 'unproc-local'
    db.create_podcast(slug, f'local://{slug}', 'Archive', feed_type='local')
    db.set_setting('unprocessed_playback', 'original', is_default=False)
    local_ep = {'url': f'local://{slug}', 'title': 'Ep 1', 'description': '',
                'artwork_url': None, 'published': None}
    feed_map = {slug: {'in': f'local://{slug}', 'out': slug}}
    try:
        with patch('main_app.routes.get_feed_map', return_value=feed_map), \
             patch('main_app.routes._lookup_episode', return_value=(local_ep, 'Archive')), \
             patch('main_app.processing.start_background_processing',
                   return_value=(True, None)) as start:
            with app.test_client() as c:
                resp = c.get(f'/episodes/{slug}/{EPISODE_ID}.mp3')
        assert resp.status_code == 503
        start.assert_called_once()
    finally:
        db.delete_podcast(slug)


def test_import_and_patch_share_one_rss_field_set():
    import config
    import config_transfer
    assert {'processed_title_marker', 'unprocessed_playback'} <= config.RSS_AFFECTING_FEED_FIELDS
    assert config.DEFERRED_RSS_FEED_FIELDS <= config.RSS_AFFECTING_FEED_FIELDS
    assert config_transfer.RSS_AFFECTING_FEED_FIELDS is config.RSS_AFFECTING_FEED_FIELDS


def _get_unresolved():
    with patch('main_app.routes.get_feed_map', return_value=FEED_MAP), \
         patch('main_app.routes._lookup_episode', return_value=(None, None)), \
         patch('main_app.processing.start_background_processing') as start:
        with app.test_client() as c:
            resp = c.get(f'/episodes/{SLUG}/{EPISODE_ID}.mp3')
    return resp, start


def test_unresolvable_episode_keeps_the_previous_status_handling():
    """Original mode only guarantees playback never starts a JIT run; when the
    upstream URL cannot be resolved the usual status responses still apply."""
    db.set_setting('unprocessed_playback', 'original', is_default=False)
    db.upsert_episode(SLUG, EPISODE_ID, original_url=EP['url'], title='Ep 1',
                      status='permanently_failed')
    resp, start = _get_unresolved()
    assert resp.status_code == 410
    start.assert_not_called()

    db.upsert_episode(SLUG, EPISODE_ID, status='processing')
    resp, start = _get_unresolved()
    assert resp.status_code == 503 and resp.headers.get('Retry-After') == '30'
    start.assert_not_called()

    db.upsert_episode(SLUG, EPISODE_ID, status='processed')
    with patch('main_app.routes._published_processed_path', return_value=None):
        resp, start = _get_unresolved()
    assert resp.status_code == 404
    start.assert_not_called()

    with db.get_connection() as conn:
        conn.execute("UPDATE episodes SET approval_pending_at='2026-11-05T10:00:00Z' "
                     "WHERE episode_id=?", (EPISODE_ID,))
    resp, start = _get_unresolved()
    assert resp.status_code == 503
    start.assert_not_called()


def test_feed_and_global_validation_share_one_validator_and_message():
    import api.feeds as feeds_api
    from config import UNPROCESSED_PLAYBACK_VALUES, normalize_override
    expected = normalize_override('x', UNPROCESSED_PLAYBACK_VALUES, 'unprocessedPlayback')[1]
    assert feeds_api._normalize_override is normalize_override
    with app.test_client() as c:
        glob = c.put('/api/v1/settings/ad-detection', json={'unprocessedPlayback': 'x'})
        assert glob.status_code == 400 and expected in glob.get_data(as_text=True)
        assert c.put('/api/v1/settings/ad-detection',
                     json={'unprocessedPlayback': None}).status_code == 400
        feed = c.patch(f'/api/v1/feeds/{SLUG}', json={'unprocessedPlayback': 'x'})
        assert feed.status_code == 400 and expected in feed.get_data(as_text=True)
