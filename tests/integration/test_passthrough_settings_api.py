"""Integration tests for the #521 feed API surface: websiteUrl exposure
and the passthroughEnabled setting round-trip."""
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='pt-api-test-'))


@pytest.fixture
def seeded_feed(app_client):
    from api import get_database
    db = get_database()
    slug = 'pt-api-feed'
    db.create_podcast(slug, 'https://example.com/feed.xml', 'PT API Test')
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


def test_feed_exposes_website_url_and_passthrough(app_client, seeded_feed):
    db, slug = seeded_feed['db'], seeded_feed['slug']
    db.update_podcast(slug, website_url='https://www.example.com/',
                      passthrough_enabled=1)

    _authed(app_client)
    resp = app_client.get(f'/api/v1/feeds/{slug}')
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['websiteUrl'] == 'https://www.example.com/'
    assert data['passthroughEnabled'] is True


def test_patch_passthrough_round_trip(app_client, seeded_feed):
    slug = seeded_feed['slug']
    _authed(app_client)
    headers = _csrf_headers(app_client)

    resp = app_client.patch(f'/api/v1/feeds/{slug}',
                            json={'passthroughEnabled': True}, headers=headers)
    assert resp.status_code == 200
    assert app_client.get(f'/api/v1/feeds/{slug}').get_json()['passthroughEnabled'] is True

    resp = app_client.patch(f'/api/v1/feeds/{slug}',
                            json={'passthroughEnabled': False}, headers=headers)
    assert resp.status_code == 200
    assert app_client.get(f'/api/v1/feeds/{slug}').get_json()['passthroughEnabled'] is False


def test_patch_skip_ad_detection_round_trip(app_client, seeded_feed):
    slug = seeded_feed['slug']
    _authed(app_client)
    headers = _csrf_headers(app_client)

    assert app_client.get(f'/api/v1/feeds/{slug}').get_json()['skipAdDetection'] is None

    resp = app_client.patch(f'/api/v1/feeds/{slug}',
                            json={'skipAdDetection': True}, headers=headers)
    assert resp.status_code == 200
    assert app_client.get(f'/api/v1/feeds/{slug}').get_json()['skipAdDetection'] is True

    resp = app_client.patch(f'/api/v1/feeds/{slug}',
                            json={'skipAdDetection': None}, headers=headers)
    assert resp.status_code == 200
    assert app_client.get(f'/api/v1/feeds/{slug}').get_json()['skipAdDetection'] is None
