"""Authenticated feeds end-to-end (2.33.0): 401 enforcement on the public
feed surface, admin/API exemption, serve_rss self-heal, and the settings/
feeds API lifecycle (enable, key exposure, rotation, keyed URLs, OPML).
"""
import io
import os
import re
import sys
import tempfile
from unittest.mock import patch

import pytest
from PIL import Image

_test_data_dir = tempfile.mkdtemp(prefix='feedauth_test_')
os.environ.setdefault('SECRET_KEY', 'feedauth-test-secret')
os.environ.setdefault('DATA_DIR', _test_data_dir)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

import database
import storage as storage_mod
database.Database._instance = None
database.Database.__init__.__defaults__ = (_test_data_dir,)
storage_mod.Storage.__init__.__defaults__ = (_test_data_dir,)

from main_app import app, db as app_db
import main_app.feeds as feeds_mod
import main_app.routes as routes_mod

KEY = 'e' * 64
OTHER_KEY = 'f' * 64
BASE = 'http://localhost:8000'


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new('RGB', (300, 300), (255, 255, 255)).save(buf, 'PNG')
    return buf.getvalue()


def _cached_rss(slug, key=None):
    suffix = f'?key={key}' if key else ''
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>T</title>
<item><enclosure url="{BASE}/episodes/{slug}/abcdefabcdef.mp3{suffix}" type="audio/mpeg" /></item>
</channel></rss>"""


def _set_auth(db, enabled, key=KEY):
    db.set_setting('feed_auth_enabled', 'true' if enabled else 'false',
                   is_default=False)
    db.set_setting('feed_auth_key', key or '', is_default=False)


@pytest.fixture(autouse=True)
def _bind_db_singleton():
    """Pin the Database singleton to the instance main_app's routes read.

    Every test module resets Database._instance at import time, so under a
    full-suite run get_database() would otherwise return a different instance
    (and data dir) than main_app.db - making settings written via the API
    invisible to the public-route feed key gate.
    """
    prev = database.Database._instance
    database.Database._instance = app_db
    yield
    _set_auth(app_db, False)  # never leak enabled state across tests
    database.Database._instance = prev


@pytest.fixture
def client():
    app_db.set_setting('app_password', '')
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c


@pytest.fixture
def db():
    return app_db


def _seed_feed(db, slug, key=None):
    if not db.get_podcast_by_slug(slug):
        db.create_podcast(slug, f'https://example.com/{slug}.xml', slug)
    routes_mod.storage.save_rss(slug, _cached_rss(slug, key))
    feeds_mod.invalidate_feed_cache()


# --- 401 matrix when enabled -------------------------------------------------

PUBLIC_PATHS = [
    '/auth-feed',
    '/episodes/auth-feed/abcdefabcdef.mp3',
    '/episodes/auth-feed/abcdefabcdef-v2.mp3',
    '/episodes/auth-feed/abcdefabcdef.vtt',
    '/episodes/auth-feed/abcdefabcdef/chapters.json',
    '/auth-feed/cover-minuspod.jpg',
    '/auth-feed/cover-minuspod-deadbeef.jpg',
    '/episodes/auth-feed/cover-minuspod.jpg',
]


@pytest.mark.parametrize('path', PUBLIC_PATHS)
def test_401_without_key(client, db, path):
    _set_auth(db, True)
    assert client.get(path).status_code == 401
    assert client.head(path).status_code == 401  # HEAD parity


@pytest.mark.parametrize('path', PUBLIC_PATHS)
def test_401_with_wrong_key(client, db, path):
    _set_auth(db, True)
    assert client.get(f'{path}?key={OTHER_KEY}').status_code == 401


def test_all_open_when_disabled(client, db):
    _set_auth(db, False)
    _seed_feed(db, 'open-feed')
    assert client.get('/open-feed').status_code == 200
    # unknown mp3 reaches the handler (404), not the key gate (401)
    assert client.get('/episodes/open-feed/abcdefabcdef.vtt').status_code == 404


def test_rss_200_with_key(client, db):
    _set_auth(db, True)
    _seed_feed(db, 'auth-feed', key=KEY)
    resp = client.get(f'/auth-feed?key={KEY}')
    assert resp.status_code == 200
    assert f'?key={KEY}' in resp.get_data(as_text=True)


def test_mp3_with_valid_key_reaches_handler(client, db):
    _set_auth(db, True)
    _seed_feed(db, 'auth-feed', key=KEY)
    # Unknown episode: with a valid key the request passes the gate and gets
    # the handler's own answer (JIT flow), never the 401.
    resp = client.get(f'/episodes/auth-feed/aaaaaaaaaaaa.mp3?key={KEY}')
    assert resp.status_code != 401


def test_cover_with_path_key_200(client, db):
    _set_auth(db, True)
    slug = 'auth-art'
    if not db.get_podcast_by_slug(slug):
        db.create_podcast(slug, f'https://example.com/{slug}.xml', slug)
    routes_mod.storage.save_artwork(slug, _png(), 'image/png',
                                    'https://example.com/a.png')
    version = routes_mod.storage.artwork_version(slug)
    assert client.get(
        f'/{slug}/cover-minuspod-{version}-{KEY}.jpg').status_code == 200
    assert client.get(
        f'/{slug}/cover-minuspod-{version}.jpg').status_code == 401


def test_admin_and_infra_paths_unaffected(client, db):
    _set_auth(db, True)
    assert client.get('/health').status_code == 200
    assert client.get('/favicon.ico').status_code == 200
    assert client.get('/apple-touch-icon.png').status_code == 200
    assert client.get('/api/v1/feeds').status_code == 200
    assert client.get('/api/v1/health').status_code == 200


# --- serve_rss self-heal ------------------------------------------------------

def test_serve_rss_self_heals_keyless_cache(client, db):
    _set_auth(db, True)
    slug = 'heal-feed'
    _seed_feed(db, slug, key=None)  # stale keyless cache

    def fake_refresh(s, url, force=False):
        routes_mod.storage.save_rss(s, _cached_rss(s, KEY))
        return True

    with patch.object(feeds_mod, 'refresh_rss_feed',
                      side_effect=fake_refresh) as spy:
        resp = client.get(f'/{slug}?key={KEY}')
    assert resp.status_code == 200
    assert spy.called and spy.call_args.kwargs.get('force') is True
    assert f'?key={KEY}' in resp.get_data(as_text=True)


def test_serve_rss_no_refresh_when_key_matches(client, db):
    _set_auth(db, True)
    slug = 'heal-feed-ok'
    _seed_feed(db, slug, key=KEY)
    with patch.object(feeds_mod, 'refresh_rss_feed') as spy:
        assert client.get(f'/{slug}?key={KEY}').status_code == 200
    spy.assert_not_called()


# --- settings + feeds API lifecycle ------------------------------------------

def test_enable_via_api_generates_key_and_exposes_it(client, db):
    db.set_setting('feed_auth_key', '', is_default=False)
    _set_auth(db, False, key='')
    resp = client.put('/api/v1/settings/ad-detection',
                      json={'feedAuthEnabled': True})
    assert resp.status_code == 200
    key = db.get_setting('feed_auth_key')
    assert key and re.fullmatch(r'[0-9a-f]{64}', key)

    settings = client.get('/api/v1/settings').get_json()
    assert settings['feedAuthEnabled']['value'] is True
    assert settings['feedAuthKey'] == key
    assert settings['defaults']['feedAuthEnabled'] is False


def test_enable_rejects_non_boolean(client, db):
    # bool("false") is True: a stringly-typed disable request must 400, not
    # silently ENABLE enforcement and lock out every subscriber.
    _set_auth(db, False, key='')
    resp = client.put('/api/v1/settings/ad-detection',
                      json={'feedAuthEnabled': 'false'})
    assert resp.status_code == 400
    assert db.get_setting_bool('feed_auth_enabled', False) is False


def test_feed_urls_keyed_in_api_and_opml(client, db):
    _set_auth(db, True)
    _seed_feed(db, 'auth-feed', key=KEY)
    feeds = client.get('/api/v1/feeds').get_json()['feeds']
    assert feeds
    assert all(f'?key={KEY}' in f['feedUrl'] for f in feeds)

    opml = client.get('/api/v1/feeds/export-opml?mode=modified')
    assert opml.status_code == 200
    assert f'?key={KEY}' in opml.get_data(as_text=True)
    original = client.get('/api/v1/feeds/export-opml?mode=original')
    assert '?key=' not in original.get_data(as_text=True)


def test_episode_urls_keyed_in_api(client, db):
    _set_auth(db, True)
    slug, ep_id = 'auth-feed', 'abcdefabcdef'
    _seed_feed(db, slug, key=KEY)
    db.bulk_upsert_discovered_episodes(slug, [{
        'id': ep_id, 'title': 'Ep', 'url': 'https://example.com/e.mp3',
        'published': 'Mon, 01 Jan 2026 00:00:00 +0000', 'description': 'd'}])
    detail = client.get(f'/api/v1/feeds/{slug}/episodes/{ep_id}').get_json()
    assert f'?key={KEY}' in detail['processedUrl']


def test_regenerate_key_rotates_and_409_when_disabled(client, db):
    _set_auth(db, True, key=KEY)
    resp = client.post('/api/v1/settings/feed-auth/regenerate-key')
    assert resp.status_code == 200
    new_key = resp.get_json()['feedAuthKey']
    assert re.fullmatch(r'[0-9a-f]{64}', new_key) and new_key != KEY
    # old key rejected immediately on the public surface
    _seed_feed(db, 'auth-feed', key=new_key)
    assert client.get(f'/auth-feed?key={KEY}').status_code == 401
    assert client.get(f'/auth-feed?key={new_key}').status_code == 200

    _set_auth(db, False)
    assert client.post(
        '/api/v1/settings/feed-auth/regenerate-key').status_code == 409


def test_feeds_regenerate_endpoint(client, db):
    _set_auth(db, True)
    _seed_feed(db, 'auth-feed', key=None)
    with patch.object(feeds_mod.rss_parser, 'fetch_feed',
                      return_value=_cached_rss('auth-feed')):
        resp = client.post('/api/v1/feeds/regenerate')
    assert resp.status_code == 200
    body = resp.get_json()
    assert body['feedCount'] >= 1
    # the rebuild embedded the active key
    assert f'?key={KEY}' in routes_mod.storage.get_rss('auth-feed')
