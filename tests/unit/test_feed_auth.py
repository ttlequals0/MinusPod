"""Authenticated feeds: key generation, cover-token parsing, and the
require_feed_key route decorator (2.33.0).

The decorator reads the real main_app.db singleton (lazy import), so this file
uses the standard test-data-dir bootstrap and drives the setting rows directly.
"""
import atexit
import os
import shutil
import sys
import tempfile

import pytest

_test_data_dir = tempfile.mkdtemp(prefix='feed_auth_test_')
os.environ.setdefault('SECRET_KEY', 'test-secret')
os.environ['DATA_DIR'] = _test_data_dir

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

import database
import storage as storage_mod

database.Database._instance = None
database.Database.__init__.__defaults__ = (_test_data_dir,)
database.Database.__new__.__defaults__ = (_test_data_dir,)
storage_mod.Storage._instance = None
storage_mod.Storage.__init__.__defaults__ = (_test_data_dir,)
atexit.register(shutil.rmtree, _test_data_dir, ignore_errors=True)

from flask import Flask

from main_app import db
from main_app.feed_auth import (
    KEY_RE,
    active_feed_key,
    extract_key_from_cover_token,
    feed_auth_enabled,
    generate_feed_key,
    require_feed_key,
)

KEY = 'a' * 64
OTHER_KEY = 'b' * 64
VERSION = 'deadbeef'  # 8-hex artwork version token


def _set_auth(enabled, key=KEY):
    db.set_setting('feed_auth_enabled', 'true' if enabled else 'false',
                   is_default=False)
    db.set_setting('feed_auth_key', key or '', is_default=False)


@pytest.fixture(autouse=True)
def _reset_feed_auth():
    """The Database singleton is shared across test modules in one pytest
    process; never leak an enabled feed-auth state into other files."""
    yield
    db.set_setting('feed_auth_enabled', 'false', is_default=False)


@pytest.fixture
def flask_client():
    app = Flask(__name__)
    app.config['TESTING'] = True

    @app.route('/<slug>')
    @require_feed_key
    def feed(slug):
        return 'rss-ok'

    @app.route('/<slug>/cover-minuspod-<token>.jpg')
    @require_feed_key
    def cover(slug, token=None):
        return 'cover-ok'

    with app.test_client() as c:
        yield c


# --- key generation and token parsing --------------------------------------

def test_generate_feed_key_is_64_hex():
    key = generate_feed_key()
    assert KEY_RE.fullmatch(key)
    assert generate_feed_key() != key  # random


def test_extract_key_from_cover_token_matrix():
    assert extract_key_from_cover_token(None) is None
    assert extract_key_from_cover_token('') is None
    assert extract_key_from_cover_token(VERSION) is None  # version only
    assert extract_key_from_cover_token(KEY) == KEY  # key only
    assert extract_key_from_cover_token(f'{VERSION}-{KEY}') == KEY  # combo
    # token_urlsafe-style garbage (hyphens/underscores) never matches
    assert extract_key_from_cover_token('aB_c-' * 12) is None
    # uppercase hex rejected (keys are lowercase by construction)
    assert extract_key_from_cover_token(KEY.upper()) is None
    # off-by-one lengths rejected
    assert extract_key_from_cover_token('a' * 63) is None
    assert extract_key_from_cover_token('a' * 65) is None


# --- setting readers ---------------------------------------------------------

def test_active_feed_key_states():
    _set_auth(False, KEY)
    assert feed_auth_enabled(db) is False
    assert active_feed_key(db) is None  # disabled: keyless serving

    _set_auth(True, KEY)
    assert feed_auth_enabled(db) is True
    assert active_feed_key(db) == KEY

    _set_auth(True, '')
    assert active_feed_key(db) is None  # enabled but empty: no usable key


# --- decorator enforcement ---------------------------------------------------

def test_decorator_noop_when_disabled(flask_client):
    _set_auth(False, KEY)
    assert flask_client.get('/some-feed').status_code == 200


def test_decorator_401_when_missing_key(flask_client):
    _set_auth(True, KEY)
    assert flask_client.get('/some-feed').status_code == 401


def test_decorator_401_when_wrong_key(flask_client):
    _set_auth(True, KEY)
    assert flask_client.get(f'/some-feed?key={OTHER_KEY}').status_code == 401


def test_decorator_401_not_500_on_non_ascii_key(flask_client):
    # compare_digest raises TypeError on non-ASCII strings; the KEY_RE
    # prefilter must turn garbage keys into a clean 401, never a 500.
    _set_auth(True, KEY)
    resp = flask_client.get('/some-feed?key=%C3%A9%C3%A9caf%C3%A9')
    assert resp.status_code == 401


def test_decorator_200_with_query_key(flask_client):
    _set_auth(True, KEY)
    resp = flask_client.get(f'/some-feed?key={KEY}')
    assert resp.status_code == 200
    assert resp.data == b'rss-ok'


def test_decorator_200_with_cover_path_token(flask_client):
    _set_auth(True, KEY)
    assert flask_client.get(
        f'/some-feed/cover-minuspod-{VERSION}-{KEY}.jpg').status_code == 200
    assert flask_client.get(
        f'/some-feed/cover-minuspod-{KEY}.jpg').status_code == 200
    # version-only token carries no key
    assert flask_client.get(
        f'/some-feed/cover-minuspod-{VERSION}.jpg').status_code == 401


def test_decorator_head_parity(flask_client):
    _set_auth(True, KEY)
    assert flask_client.head('/some-feed').status_code == 401
    assert flask_client.head(f'/some-feed?key={KEY}').status_code == 200


def test_decorator_fails_closed_on_empty_stored_key(flask_client):
    _set_auth(True, '')
    # Even an empty supplied key must not match an empty stored key.
    assert flask_client.get('/some-feed').status_code == 401
    assert flask_client.get('/some-feed?key=').status_code == 401


def test_decorator_logs_exact_line(flask_client, caplog):
    _set_auth(True, KEY)
    with caplog.at_level('WARNING', logger='podcast.feed'):
        flask_client.get('/some-feed')
    assert any('no auth key provided or is invalid' in r.message
               for r in caplog.records)
    # the key value itself never appears in logs
    assert all(KEY not in r.message for r in caplog.records)


class TestEnsureFeedAuthKey:
    """Env-seeded FEED_AUTH_ENABLED=true must not fail closed with no key:
    boot mints one exactly like the UI enable path (and clears etags so the
    refresher re-renders feeds with the new auth state)."""

    def _db(self, enabled, key):
        from unittest.mock import MagicMock
        db = MagicMock()
        db.get_setting_bool.return_value = enabled
        db.get_setting.return_value = key
        return db

    def test_mints_key_when_enabled_without_one(self):
        from main_app.feed_auth import ensure_feed_auth_key
        db = self._db(True, None)
        ensure_feed_auth_key(db)
        args, kwargs = db.set_setting.call_args
        assert args[0] == 'feed_auth_key' and args[1]
        assert kwargs.get('is_default') is False
        db.clear_all_podcast_etags.assert_called_once()

    def test_noop_when_key_exists(self):
        from main_app.feed_auth import ensure_feed_auth_key
        db = self._db(True, 'existing-key')
        ensure_feed_auth_key(db)
        db.set_setting.assert_not_called()

    def test_noop_when_disabled(self):
        from main_app.feed_auth import ensure_feed_auth_key
        db = self._db(False, None)
        ensure_feed_auth_key(db)
        db.set_setting.assert_not_called()
