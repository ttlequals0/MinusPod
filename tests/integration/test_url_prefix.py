"""The app served under a reverse-proxy path prefix (MINUSPOD_URL_PREFIX)."""
import json
import re
from pathlib import Path

import pytest
from werkzeug.security import generate_password_hash

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('urlprefix_test_', secret_key='urlprefix-test-secret')

import config
import database
from api import system
from main_app import app, routes

PREFIX = '/minuspod'
STATIC_UI = Path(__file__).resolve().parents[2] / 'static' / 'ui'


@pytest.fixture
def prefixed(monkeypatch):
    monkeypatch.setattr(config, 'URL_PREFIX', PREFIX)
    monkeypatch.setitem(app.config, 'SESSION_COOKIE_PATH', PREFIX)
    routes._prefixed_ui_bodies.cache_clear()
    system._render_openapi_yaml.cache_clear()
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c
    routes._prefixed_ui_bodies.cache_clear()
    system._render_openapi_yaml.cache_clear()


@pytest.fixture
def plain():
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c


@pytest.fixture
def with_password():
    db = database.Database()
    db.set_setting('app_password', generate_password_hash('pw', method='scrypt'))
    yield
    db.set_setting('app_password', '')


def test_root_redirect_carries_prefix(prefixed):
    response = prefixed.get('/')
    assert response.status_code == 302
    assert response.headers['Location'].endswith('/minuspod/ui/')


def test_health_served_prefixed_and_bare(prefixed):
    assert prefixed.get('/minuspod/api/v1/health').status_code == 200
    assert prefixed.get('/api/v1/health').status_code == 200


def test_auth_exempt_paths_work_under_prefix(prefixed, with_password):
    assert prefixed.get('/minuspod/api/v1/auth/status').status_code == 200
    assert prefixed.get('/minuspod/api/v1/feeds').status_code == 401


@pytest.mark.parametrize('path', ['/minuspod/ui/', '/minuspod/ui/settings/example'])
def test_index_html_is_rewritten(prefixed, path):
    response = prefixed.get(path)
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert '<meta name="minuspod-url-prefix" content="/minuspod">' in html
    refs = re.findall(r'(?:src|href)="(/[^"]*)"', html)
    assert refs and all(r.startswith('/minuspod/ui/') for r in refs)
    assert response.headers['Cache-Control'] == 'no-cache, must-revalidate'


def test_manifest_is_rewritten(prefixed):
    response = prefixed.get('/minuspod/ui/manifest.webmanifest')
    manifest = json.loads(response.get_data(as_text=True))
    assert manifest['start_url'] == '/minuspod/ui/'
    assert manifest['scope'] == '/minuspod/ui/'
    assert response.headers['Cache-Control'] == 'no-cache, must-revalidate'


def test_service_worker_is_rewritten(prefixed):
    response = prefixed.get('/minuspod/ui/sw.js')
    body = response.get_data(as_text=True)
    assert '/minuspod/ui/index.html' in body
    assert '"/ui/' not in body
    assert response.headers['Cache-Control'] == 'no-cache, must-revalidate'


def test_session_and_csrf_cookies_use_prefix_path(prefixed, with_password):
    login = prefixed.post('/minuspod/api/v1/auth/login', json={'password': 'pw'})
    assert login.status_code == 200
    cookies = login.headers.getlist('Set-Cookie')
    session = [h for h in cookies if h.startswith('session=')]
    assert session and 'Path=/minuspod' in session[0]
    csrf = [h for h in cookies if h.startswith('minuspod_csrf=')]
    assert csrf and 'Path=/minuspod' in csrf[0]


def test_processed_url_carries_prefix(prefixed):
    db = database.Database()
    slug = 'prefix-feed'
    db.create_podcast(slug, 'https://example.com/feed.xml', title='Prefix Feed')
    db.upsert_episode(slug, 'pfx-ep-1', original_url='https://example.com/e1.mp3',
                      title='Episode', status='processed', original_duration=3600.0)
    db.save_episode_details(slug, 'pfx-ep-1', ad_markers=[
        {'start': 200.0, 'end': 230.0, 'confidence': 0.6, 'held_for_review': True, 'was_cut': False}])
    try:
        body = prefixed.get('/minuspod/api/v1/detections').get_json()
        urls = [d['processedUrl'] for d in body['detections'] if d['feedSlug'] == slug]
        assert urls and all(u.startswith('/minuspod/episodes/') for u in urls)
    finally:
        db.delete_podcast(slug)


def test_swagger_page_and_spec_carry_prefix(prefixed):
    html = prefixed.get('/minuspod/api/v1/docs').get_data(as_text=True)
    assert '/minuspod/ui/swagger/swagger-ui.css' in html
    assert '/minuspod/ui/swagger-init.js' in html
    assert 'minuspod-url-prefix' in html
    spec = prefixed.get('/minuspod/api/v1/openapi.yaml').get_data(as_text=True)
    assert 'url: /minuspod/api/v1' in spec
    assert 'url: /minuspod/\n' in spec


@pytest.mark.parametrize('name', ['index.html', 'sw.js', 'manifest.webmanifest'])
def test_unset_prefix_serves_files_byte_for_byte(plain, name):
    assert plain.get(f'/ui/{name}').get_data() == (STATIC_UI / name).read_bytes()


def test_unset_prefix_leaves_swagger_and_spec_untouched(plain):
    assert 'minuspod-url-prefix' not in plain.get('/api/v1/docs').get_data(as_text=True)
    assert 'url: /api/v1' in plain.get('/api/v1/openapi.yaml').get_data(as_text=True)
