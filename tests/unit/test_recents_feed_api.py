"""Recents feed API (#721)."""
from unittest.mock import patch

import pytest

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('recents_api_test_')

from main_app import app  # noqa: E402
import database  # noqa: E402
from database.podcasts import RECENTS_SLUG  # noqa: E402


@pytest.fixture
def client():
    app.config['TESTING'] = True
    # POST /feeds is rate limited; clear the in-memory counters per test.
    from api import limiter
    limiter.reset()
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess['authenticated'] = True
        c.get('/api/v1/auth/status')
        yield c
    db = database.Database()
    for slug in (RECENTS_SLUG, 'alpha'):
        db.delete_podcast(slug)


def _csrf(client):
    cookie = client.get_cookie('minuspod_csrf')
    return {'X-CSRF-Token': cookie.value} if cookie else {}


def _create(client, **body):
    return client.post('/api/v1/feeds', json={'feedType': 'recents', **body}, headers=_csrf(client))


def _create_ok(client, **body):
    resp = _create(client, **body)
    assert resp.status_code == 201, resp.data
    return resp


def test_create_returns_the_fixed_slug_and_defaults(client):
    resp = _create(client)
    assert resp.status_code == 201, resp.data
    body = resp.get_json()
    assert body['slug'] == RECENTS_SLUG and body['feedType'] == 'recents'
    feed = client.get(f'/api/v1/feeds/{RECENTS_SLUG}').get_json()
    assert feed['title'] == 'Recents' and feed['feedType'] == 'recents'
    assert feed['hasArtwork'] is True
    assert client.get(f'/api/v1/feeds/{RECENTS_SLUG}/artwork').mimetype == 'image/png'


def test_second_create_is_409(client):
    assert _create(client, title='One').status_code == 201
    assert _create(client, title='Two').status_code == 409


def test_patch_allows_title_and_description_only(client):
    _create_ok(client)
    ok = client.patch(f'/api/v1/feeds/{RECENTS_SLUG}', json={'title': 'New', 'description': 'd'},
                      headers=_csrf(client))
    assert ok.status_code == 200, ok.data
    assert ok.get_json()['title'] == 'New'
    bad = client.patch(f'/api/v1/feeds/{RECENTS_SLUG}', json={'chaptersMode': 'off'}, headers=_csrf(client))
    assert bad.status_code == 400


def test_counts_come_from_membership(client):
    db = database.Database()
    db.create_podcast('alpha', 'https://example.com/alpha.xml', 'Alpha')
    _create_ok(client)
    conn = db.get_connection()
    conn.execute("UPDATE podcasts SET created_at = '2026-09-01T00:00:00Z' WHERE slug = ?", (RECENTS_SLUG,))
    conn.commit()
    db.upsert_episode('alpha', 'aaaaaaaaaaa1', original_url='u', title='t', status='processed',
                      published_at='2026-09-10T00:00:00Z', processed_file='/a.mp3')
    feed = client.get(f'/api/v1/feeds/{RECENTS_SLUG}').get_json()
    assert feed['episodeCount'] == 1 and feed['processedCount'] == 1


def test_edit_rebuilds_the_served_feed(client):
    _create_ok(client)
    with patch('api.feeds.rebuild_recents_feed') as rebuild:
        client.patch(f'/api/v1/feeds/{RECENTS_SLUG}', json={'title': 'Again'}, headers=_csrf(client))
    rebuild.assert_called_once()


def test_delete_removes_the_row(client):
    _create_ok(client)
    assert client.delete(f'/api/v1/feeds/{RECENTS_SLUG}', headers=_csrf(client)).status_code == 200
    assert client.get(f'/api/v1/feeds/{RECENTS_SLUG}').status_code == 404


def test_opml_excludes_the_recents_feed(client):
    from utils.opml import build_opml_xml
    _create(client)
    podcasts = database.Database().get_all_podcasts()
    xml = build_opml_xml(podcasts, 'modified', 'https://mp.example.com', None)
    assert '/recents' not in xml


def test_episode_list_for_recents_carries_the_source_slug(client):
    db = database.Database()
    db.create_podcast('alpha', 'https://example.com/alpha.xml', 'Alpha')
    _create_ok(client)
    conn = db.get_connection()
    conn.execute("UPDATE podcasts SET created_at = '2026-09-01T00:00:00Z' WHERE slug = ?", (RECENTS_SLUG,))
    conn.commit()
    db.upsert_episode('alpha', 'aaaaaaaaaaa1', original_url='u', title='t', status='processed',
                      published_at='2026-09-10T00:00:00Z', processed_file='/a.mp3')
    body = client.get(f'/api/v1/feeds/{RECENTS_SLUG}/episodes?limit=10').get_json()
    assert body['total'] == 1
    assert body['episodes'][0]['feedSlug'] == 'alpha'
    assert body['episodes'][0]['feedTitle'] == 'Alpha'
    assert body['episodes'][0]['id'] == 'aaaaaaaaaaa1'
