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
