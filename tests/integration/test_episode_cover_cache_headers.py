"""Versioned cover responses must be reusable from the browser's private cache.

Flask adds `Vary: Cookie` whenever the session was read, and the session cookie is re-signed on
other API responses, so a cover keyed on Cookie never matched its next request and was revalidated."""
import json
from io import BytesIO

import pytest
from PIL import Image
from werkzeug.security import generate_password_hash

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('cover_cache_test_', secret_key='cover-cache-test-secret')

import api as api_module  # noqa: E402
import database  # noqa: E402
import storage as storage_module  # noqa: E402
from main_app import app  # noqa: E402

PW = 'correct-password-123456'
ADDR = {'REMOTE_ADDR': '192.168.1.5'}
SLUG = 'cover-cache-feed'
EP = 'a1b2c3d4e5f6'
COVER_URL = 'https://cdn.example/cover.jpg'
LONG = 'private, max-age=604800, immutable'


def _jpeg():
    out = BytesIO()
    Image.new('RGB', (600, 600), (10, 120, 200)).save(out, 'JPEG')
    return out.getvalue()


@pytest.fixture
def client():
    db = database.Database()
    db.set_setting('app_password', generate_password_hash(PW, method='scrypt'))
    app.config['TESTING'] = True
    api_module.limiter.enabled = False
    conn = db.get_connection()
    conn.execute("DELETE FROM podcasts WHERE slug = ?", (SLUG,))
    conn.commit()
    podcast_id = db.create_podcast(slug=SLUG, source_url='https://example.com/rss.xml', title='Cover Cache')
    conn.execute(
        "INSERT INTO episodes (podcast_id, episode_id, title, original_url, artwork_url) "
        "VALUES (?, ?, 'E1', 'https://e/1.mp3', ?)", (podcast_id, EP, COVER_URL))
    conn.commit()
    storage = storage_module.Storage()
    storage._write_cover(SLUG, storage_module.artwork_url_key(COVER_URL), _jpeg(), 'image/jpeg')
    with app.test_client() as c:
        yield c
    conn.execute("DELETE FROM podcasts WHERE slug = ?", (SLUG,))
    conn.commit()
    db.set_setting('app_password', '')
    api_module.limiter.enabled = True


def _login(client):
    return client.post('/api/v1/auth/login', data=json.dumps({'password': PW, 'rememberMe': True}),
                       content_type='application/json', environ_base=ADDR)


def _thumb(client, version=None, **kwargs):
    version = version or storage_module.artwork_url_version(COVER_URL)
    return client.get(f'/api/v1/feeds/{SLUG}/episodes/{EP}/artwork?size=thumb&v={version}',
                      environ_base=ADDR, **kwargs)


def test_other_api_responses_re_sign_the_session_cookie(client):
    assert _login(client).status_code == 200
    status = client.get('/api/v1/auth/status', environ_base=ADDR)

    assert any(h.startswith('session=') for h in status.headers.getlist('Set-Cookie'))


def test_a_versioned_cover_can_be_reused_across_session_cookie_changes(client):
    assert _login(client).status_code == 200
    first = _thumb(client)
    client.get('/api/v1/auth/status', environ_base=ADDR)
    second = _thumb(client)

    for response in (first, second):
        assert response.status_code == 200
        assert response.headers['Cache-Control'] == LONG
        assert 'Cookie' not in response.headers.get('Vary', '')
        assert 'Expires' not in response.headers
        assert response.headers['ETag'] and response.headers['Last-Modified']
        assert response.headers['X-Content-Type-Options'] == 'nosniff'
        assert response.headers['Content-Security-Policy'] == "default-src 'none'"


def test_an_unversioned_cover_keeps_revalidating_and_varying_on_cookie(client):
    assert _login(client).status_code == 200
    response = _thumb(client, version='000000000000')

    assert response.headers['Cache-Control'] == 'no-cache'
    assert 'Cookie' in response.headers.get('Vary', '')
    assert 'Expires' in response.headers


def test_a_versioned_cover_still_needs_a_signed_in_session(client):
    response = _thumb(client)

    assert response.status_code == 401
    assert response.headers.get('Cache-Control') != LONG
