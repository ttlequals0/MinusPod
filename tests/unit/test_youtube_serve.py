"""Serving a discovered YouTube episode: first play starts processing, HEAD never does."""
from unittest.mock import patch

import pytest

from tests.app_bootstrap import bootstrap

bootstrap('youtube_serve_test_', reset_storage=True)

from main_app import app, db, storage  # noqa: E402

SLUG, EP = 'ytserve', 'a1b2c3d4e5f6'


@pytest.fixture(autouse=True)
def _youtube_episode():
    db.create_podcast(SLUG, 'youtube://playlist/PL1', feed_type='youtube')
    db.set_setting('youtube_enabled', 'true', is_default=False)
    db.upsert_episode(SLUG, EP, original_url='https://www.youtube.com/watch?v=vid000000001',
                      status='discovered', title='Ep1', published_at='2026-10-27T00:00:00Z')
    yield
    db.delete_podcast(SLUG)


def _feed_map():
    return patch('main_app.routes.get_feed_map', return_value={SLUG: {'in': f'youtube://{SLUG}', 'out': f'/{SLUG}'}})


def test_first_play_starts_processing_stamped_as_jit():
    with _feed_map(), patch('main_app.processing.start_background_processing', return_value=(True, 'started')) as start:
        with app.test_client() as c:
            resp = c.get(f'/episodes/{SLUG}/{EP}.mp3')
    assert resp.status_code == 503 and resp.headers['Retry-After'] == '30'
    start.assert_called_once()
    assert start.call_args.args[2] == 'https://www.youtube.com/watch?v=vid000000001'
    assert db.get_episode(SLUG, EP)['reprocess_source'] == 'jit'


def test_play_keeps_an_existing_stamp():
    db.upsert_episode(SLUG, EP, reprocess_requested_at='2026-10-28T00:00:00Z', reprocess_source='manual')
    with _feed_map(), patch('main_app.processing.start_background_processing', return_value=(True, 'started')):
        with app.test_client() as c:
            c.get(f'/episodes/{SLUG}/{EP}.mp3')
    assert db.get_episode(SLUG, EP)['reprocess_source'] == 'manual'


def test_play_during_processing_serves_the_retained_original():
    db.upsert_episode(SLUG, EP, status='processing')
    path = storage.get_original_path(SLUG, EP)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'ORIGINAL')
    with _feed_map(), app.test_client() as c:
        resp = c.get(f'/episodes/{SLUG}/{EP}.mp3')
    assert resp.status_code == 200 and resp.data == b'ORIGINAL'
    path.unlink()


def test_head_for_a_discovered_episode_is_200_without_processing():
    with _feed_map(), patch('main_app.processing.start_background_processing') as start:
        with app.test_client() as c:
            resp = c.head(f'/episodes/{SLUG}/{EP}.mp3')
    assert resp.status_code == 200 and resp.headers['Content-Type'] == 'audio/mpeg'
    assert resp.data == b''
    start.assert_not_called()
