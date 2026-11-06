from unittest.mock import patch

import pytest


def _authed(client):
    with client.session_transaction() as sess:
        sess['authenticated'] = True
    client.get('/api/v1/auth/status')


def _csrf_headers(client):
    for cookie in client._cookies.values():
        if cookie.key == 'minuspod_csrf':
            return {'X-CSRF-Token': cookie.value}
    return {}


def _post(client, slug, **extra):
    return client.post('/api/v1/feeds', json={
        'sourceUrl': 'https://feeds.example.com/mode.xml', 'slug': slug, **extra,
    }, headers=_csrf_headers(client))


def test_add_feed_applies_passthrough_mode(app_client):
    _authed(app_client)
    from api import get_database
    db = get_database()
    with patch('api.feeds.validate_url'), patch('main_app.feeds.refresh_rss_feed') as refresh:
        resp = _post(app_client, 'mode-passthrough', processingMode='passthrough')
    assert resp.status_code == 201, resp.get_json()
    try:
        refresh.assert_called_once()
        assert db.get_podcast_by_slug('mode-passthrough')['passthrough_enabled'] == 1
    finally:
        db.delete_podcast('mode-passthrough')


def test_add_feed_rejects_cue_only_mode(app_client):
    _authed(app_client)
    from api import get_database
    db = get_database()
    with patch('api.feeds.validate_url'), patch('main_app.feeds.refresh_rss_feed'):
        resp = _post(app_client, 'mode-cue-only', processingMode='cue_only')
    assert resp.status_code == 400
    assert 'cue_only' in resp.get_json()['error']
    assert db.get_podcast_by_slug('mode-cue-only') is None


def test_add_feed_rejects_unknown_mode(app_client):
    _authed(app_client)
    from api import get_database
    db = get_database()
    with patch('api.feeds.validate_url'), patch('main_app.feeds.refresh_rss_feed'):
        resp = _post(app_client, 'mode-bogus', processingMode='bogus')
    assert resp.status_code == 400
    assert db.get_podcast_by_slug('mode-bogus') is None


@pytest.mark.parametrize('feed_type', ['local', 'recents'])
def test_add_feed_rejects_mode_for_local_and_recents(app_client, feed_type):
    _authed(app_client)
    resp = app_client.post('/api/v1/feeds', json={
        'feedType': feed_type, 'title': 'Mode Test', 'processingMode': 'passthrough',
    }, headers=_csrf_headers(app_client))
    assert resp.status_code == 400
    assert 'processingMode' in resp.get_json()['error']
