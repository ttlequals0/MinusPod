"""Integration tests for the chaptersMode per-feed setting (#560 API surface).

Mirrors test_passthrough_settings_api.py's fixture style. Covers:
- GET echoes the raw chapters_mode column (null when unset).
- PATCH sets each valid value ('auto', 'generate', 'off').
- PATCH null resets the override.
- PATCH an invalid string -> 400, column left unchanged.
"""
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='chapters-mode-api-test-'))


@pytest.fixture
def seeded_feed(app_client):
    from api import get_database
    db = get_database()
    slug = 'chapters-mode-api-feed'
    db.create_podcast(slug, 'https://example.com/feed.xml', 'Chapters Mode API Test')
    yield {'slug': slug, 'db': db}
    db.delete_podcast(slug)


def _authed(client):
    with client.session_transaction() as sess:
        sess['authenticated'] = True
    client.get('/api/v1/auth/status')


def _csrf_headers(client):
    csrf = None
    for cookie in client._cookies.values():
        if cookie.key == 'minuspod_csrf':
            csrf = cookie.value
    return {'X-CSRF-Token': csrf} if csrf else {}


def test_get_feed_echoes_null_chapters_mode(app_client, seeded_feed):
    slug = seeded_feed['slug']
    _authed(app_client)

    resp = app_client.get(f'/api/v1/feeds/{slug}')
    assert resp.status_code == 200
    assert resp.get_json()['chaptersMode'] is None


@pytest.mark.parametrize('mode', ['auto', 'generate', 'off'])
def test_patch_sets_each_valid_value(app_client, seeded_feed, mode):
    slug = seeded_feed['slug']
    _authed(app_client)
    headers = _csrf_headers(app_client)

    resp = app_client.patch(f'/api/v1/feeds/{slug}',
                            json={'chaptersMode': mode}, headers=headers)
    assert resp.status_code == 200
    assert resp.get_json()['chaptersMode'] == mode
    assert app_client.get(f'/api/v1/feeds/{slug}').get_json()['chaptersMode'] == mode
    assert seeded_feed['db'].get_podcast_by_slug(slug)['chapters_mode'] == mode


def test_patch_null_resets_chapters_mode(app_client, seeded_feed):
    slug = seeded_feed['slug']
    _authed(app_client)
    headers = _csrf_headers(app_client)

    app_client.patch(f'/api/v1/feeds/{slug}', json={'chaptersMode': 'generate'}, headers=headers)
    resp = app_client.patch(f'/api/v1/feeds/{slug}', json={'chaptersMode': None}, headers=headers)
    assert resp.status_code == 200
    assert resp.get_json()['chaptersMode'] is None
    assert seeded_feed['db'].get_podcast_by_slug(slug)['chapters_mode'] is None


def test_patch_invalid_value_rejected_and_column_unchanged(app_client, seeded_feed):
    slug = seeded_feed['slug']
    _authed(app_client)
    headers = _csrf_headers(app_client)

    app_client.patch(f'/api/v1/feeds/{slug}', json={'chaptersMode': 'generate'}, headers=headers)
    resp = app_client.patch(f'/api/v1/feeds/{slug}',
                            json={'chaptersMode': 'bogus'}, headers=headers)
    assert resp.status_code == 400
    body = resp.get_json()
    assert 'error' in body
    assert 'chaptersMode' in body['error']
    assert seeded_feed['db'].get_podcast_by_slug(slug)['chapters_mode'] == 'generate'
