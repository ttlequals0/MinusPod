"""API integration tests for GET /system/config-export (issue #781)."""
import json
import os
import sys
import tempfile
from urllib.parse import urlsplit

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='config-export-api-test-'))


@pytest.fixture
def seeded_feed(app_client):
    from api import get_database
    db = get_database()
    slug = 'config-export-api-feed'
    prior = {key: db.get_setting(key) for key in ('feed_auth_enabled', 'feed_auth_key', 'webhooks')}
    db.create_podcast(
        slug,
        'https://user:pass@example.com/private/sentinel-feed.xml?key=upstream-secret',
        'Config Export API Test',
    )
    db.set_setting('feed_auth_enabled', 'true')
    db.set_setting('feed_auth_key', 'super-secret-feed-key')
    db.set_setting('webhooks', json.dumps([{
        'id': 'wh1',
        'url': 'https://api.pushover.net/1/messages.json?token=wh-secret-token',
        'events': ['episode.processed'],
        'secret': 'wh-secret',
        'enabled': True,
        'payloadTemplate': None,
        'contentType': 'application/json',
    }]))
    try:
        yield {'slug': slug, 'db': db}
    finally:
        db.delete_podcast(slug)
        for key, value in prior.items():
            if value is None:
                db.clear_setting(key)
            else:
                db.set_setting(key, value)


def _authed(client):
    with client.session_transaction() as sess:
        sess['authenticated'] = True


def test_unauthenticated_returns_401(app_client):
    # The blueprint serves everything unauthenticated while no app password
    # exists, so the gate only means anything once one is set.
    from api import get_database
    db = get_database()
    db.set_setting('app_password', 'pbkdf2:sha256:fake', is_default=False)
    try:
        from main_app import app
        app.config['TESTING'] = True
        with app.test_client() as anon:
            response = anon.get('/api/v1/system/config-export')
            assert response.status_code == 401
    finally:
        db.set_setting('app_password', '', is_default=False)


def test_authenticated_export_is_redacted_json_attachment(app_client, seeded_feed):
    _authed(app_client)

    response = app_client.get('/api/v1/system/config-export')

    assert response.status_code == 200
    assert response.mimetype == 'application/json'
    disposition = response.headers['Content-Disposition']
    assert disposition.startswith('attachment;')
    assert disposition.endswith('.json')

    body_text = response.get_data(as_text=True)
    assert 'key=' not in body_text
    assert 'feedAuthKey' not in body_text
    assert 'super-secret-feed-key' not in body_text
    assert 'user:pass@' not in body_text
    assert 'wh-secret' not in body_text
    assert 'upstream-secret' not in body_text
    assert 'sentinel-feed.xml' not in body_text
    assert '@' not in body_text

    data = response.get_json()
    assert set(data.keys()) == {'settings', 'feeds', 'webhooks', 'system'}
    assert any(feed['slug'] == seeded_feed['slug'] for feed in data['feeds'])
    assert data['system']['version']
    assert data['system']['exportedAt']

    feed = next(feed for feed in data['feeds'] if feed['slug'] == seeded_feed['slug'])
    assert feed['sourceFeedUrl'] == 'https://example.com'
    assert feed['sourceUrl'] == 'https://example.com'
    assert 'author' not in feed
    assert 'p20' not in feed

    base_host = urlsplit(os.environ.get('BASE_URL', 'http://localhost:8000')).hostname
    assert base_host not in body_text
    assert feed['feedUrl'] in ('https://<domain>', 'http://<domain>')

    assert isinstance(data['webhooks'], list)
    assert data['webhooks']
    for webhook in data['webhooks']:
        parts = urlsplit(webhook['url'])
        assert not parts.path
        assert not parts.query
