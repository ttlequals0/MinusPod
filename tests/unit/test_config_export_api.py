"""API integration tests for GET /system/config-export (issue #781)."""
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='config-export-api-test-'))


@pytest.fixture
def seeded_feed(app_client):
    from api import get_database
    db = get_database()
    slug = 'config-export-api-feed'
    db.create_podcast(slug, 'https://user:pass@example.com/feed.xml', 'Config Export API Test')
    db.set_setting('feed_auth_enabled', 'true')
    db.set_setting('feed_auth_key', 'super-secret-feed-key')
    yield {'slug': slug, 'db': db}
    db.delete_podcast(slug)


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

    data = response.get_json()
    assert set(data.keys()) == {'settings', 'feeds', 'system'}
    assert any(feed['slug'] == seeded_feed['slug'] for feed in data['feeds'])
    assert data['system']['version']
    assert data['system']['exportedAt']
