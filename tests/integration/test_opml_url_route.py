"""App-level OPML import-by-URL route /opml/<mode>.opml (2.34.0).

Key-gated on the public feed domain: 200 with a valid key when feed auth is
on, 401 without, 404 when feed auth is off or the mode is bad. Also covers the
settings API exposing the copyable URLs.
"""

import pytest

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('opmlurl_test_', secret_key='opmlurl-test-secret')

import database
from main_app import app, db as app_db
from utils.validation import is_valid_slug

KEY = 'e' * 64


def _set_auth(db, enabled, key=KEY):
    db.set_setting('feed_auth_enabled', 'true' if enabled else 'false',
                   is_default=False)
    db.set_setting('feed_auth_key', key or '', is_default=False)


@pytest.fixture(autouse=True)
def _bind_db_singleton():
    prev = database.Database._instance
    database.Database._instance = app_db
    if not app_db.get_podcast_by_slug('a-show'):
        app_db.create_podcast('a-show', 'https://up.example.com/a.xml', 'A Show')
    yield
    _set_auth(app_db, False)
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


def test_modified_url_200_with_key(client, db):
    _set_auth(db, True)
    resp = client.get(f'/opml/modified.opml?key={KEY}')
    assert resp.status_code == 200
    assert resp.mimetype == 'text/xml'
    # charset must appear exactly once (no doubled parameter)
    assert resp.headers['Content-Type'] == 'text/xml; charset=utf-8'
    body = resp.get_data(as_text=True)
    assert f'/a-show?key={KEY}' in body  # keyed MinusPod feed URL


def test_original_url_200_with_key_uses_source(client, db):
    _set_auth(db, True)
    resp = client.get(f'/opml/original.opml?key={KEY}')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'https://up.example.com/a.xml' in body
    assert '?key=' not in body  # original mode carries upstream URLs, no key


def test_401_without_or_wrong_key(client, db):
    _set_auth(db, True)
    assert client.get('/opml/modified.opml').status_code == 401
    assert client.get('/opml/modified.opml?key=' + 'f' * 64).status_code == 401
    assert client.head(f'/opml/modified.opml?key={KEY}').status_code == 200


def test_404_when_feed_auth_disabled(client, db):
    _set_auth(db, False)
    # Never a public feed-list leak: disabled -> 404 even with no key.
    assert client.get('/opml/modified.opml').status_code == 404


def test_404_on_bad_mode(client, db):
    _set_auth(db, True)
    assert client.get(f'/opml/bogus.opml?key={KEY}').status_code == 404


def test_settings_exposes_copy_urls_when_enabled(client, db):
    _set_auth(db, True)
    s = client.get('/api/v1/settings').get_json()
    assert s['opmlModifiedUrl'].endswith(f'/opml/modified.opml?key={KEY}')
    assert s['opmlOriginalUrl'].endswith(f'/opml/original.opml?key={KEY}')


def test_settings_copy_urls_null_when_disabled(client, db):
    _set_auth(db, False)
    s = client.get('/api/v1/settings').get_json()
    assert s['opmlModifiedUrl'] is None
    assert s['opmlOriginalUrl'] is None


def test_opml_slug_is_reserved():
    # No feed can shadow the /opml/<mode>.opml route.
    assert is_valid_slug('opml') is False
    assert is_valid_slug('a-show') is True
