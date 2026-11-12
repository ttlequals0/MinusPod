from tests.app_bootstrap import bootstrap
_test_data_dir = bootstrap('session_expiry_test_', secret_key='expiry-test-secret')
import pytest
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone
from werkzeug.security import generate_password_hash
from database import Database
from main_app import app
import api.auth_state as auth_state
from api.csrf import CSRF_COOKIE_NAME

PW = 'correct-password-123456'


@pytest.fixture
def db():
    d = Database()
    d.set_setting('app_password', generate_password_hash(PW, method='scrypt'))
    d.set_setting('session_lifetime_hours', '', is_default=True)
    d.set_setting('session_remember_days', '', is_default=True)
    yield d
    d.set_setting('app_password', '')


@pytest.fixture
def clock(monkeypatch):
    t = [1_000_000.0]
    monkeypatch.setattr(auth_state, '_now', lambda: t[0])
    return t


@pytest.fixture
def client(db):
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c


def _signed_in(client, db, clock, remembered):
    with client.session_transaction() as sess:
        sess.permanent = True
        sess['authenticated'] = True
        sess['auth_generation'] = auth_state.current_generation(db)
        sess['auth_remembered'] = remembered
        sess['auth_last_used'] = clock[0]


def _set_cookie(resp, name):
    for h in resp.headers.getlist('Set-Cookie'):
        if h.startswith(f'{name}='):
            return h
    return None


def _cookie_lifetime_seconds(header):
    attrs = {}
    for part in header.split(';')[1:]:
        key, _, value = part.strip().partition('=')
        attrs[key.lower()] = value
    if 'max-age' in attrs:
        return int(attrs['max-age'])
    expires = parsedate_to_datetime(attrs['expires'])
    return (expires - datetime.now(timezone.utc)).total_seconds()


def test_remembered_cookie_expiry_is_days_scale(client, db, clock):
    _signed_in(client, db, clock, remembered=True)
    resp = client.get('/api/v1/auth/status')
    assert resp.get_json()['authenticated'] is True
    lifetime = _cookie_lifetime_seconds(_set_cookie(resp, 'session'))
    assert abs(lifetime - 30 * 86400) < 60


def test_normal_cookie_expiry_matches_hours_setting(client, db, clock):
    db.set_setting('session_lifetime_hours', '5', is_default=False)
    _signed_in(client, db, clock, remembered=False)
    resp = client.get('/api/v1/auth/status')
    lifetime = _cookie_lifetime_seconds(_set_cookie(resp, 'session'))
    assert abs(lifetime - 5 * 3600) < 60


def test_changed_days_setting_moves_the_cookie_expiry(client, db, clock):
    db.set_setting('session_remember_days', '3', is_default=False)
    _signed_in(client, db, clock, remembered=True)
    resp = client.get('/api/v1/auth/status')
    assert abs(_cookie_lifetime_seconds(_set_cookie(resp, 'session')) - 3 * 86400) < 60


def test_anonymous_response_still_sets_no_cookie(client):
    resp = client.get('/api/v1/auth/status')
    assert _set_cookie(resp, 'session') is None
    assert _set_cookie(resp, CSRF_COOKIE_NAME) is None


@pytest.mark.parametrize('path', ['/api/v1/auth/status', '/api/v1/feeds', '/ui/'])
def test_expired_session_is_cleared_without_a_csrf_cookie(client, db, clock, path):
    _signed_in(client, db, clock, remembered=False)
    clock[0] += 24 * 3600 + 1
    resp = client.get(path)
    assert _set_cookie(resp, CSRF_COOKIE_NAME) is None
    cleared = _set_cookie(resp, 'session')
    assert cleared is not None and cleared.startswith('session=;')
    assert client.get('/api/v1/auth/status').get_json()['authenticated'] is False


def test_expired_session_gets_401_from_the_api(client, db, clock):
    _signed_in(client, db, clock, remembered=False)
    clock[0] += 24 * 3600 + 1
    assert client.get('/api/v1/feeds').status_code == 401


def test_expired_session_mutation_is_401_not_csrf_403(client, db, clock):
    _signed_in(client, db, clock, remembered=False)
    clock[0] += 24 * 3600 + 1
    assert client.post('/api/v1/feeds', json={}).status_code == 401


def test_expired_session_can_still_log_out(client, db, clock):
    _signed_in(client, db, clock, remembered=False)
    clock[0] += 24 * 3600 + 1
    assert client.post('/api/v1/auth/logout').status_code == 200


def test_remembered_session_survives_a_normal_limit(client, db, clock):
    _signed_in(client, db, clock, remembered=True)
    clock[0] += 24 * 3600 + 1
    resp = client.get('/api/v1/auth/status')
    assert resp.get_json()['authenticated'] is True
    assert _set_cookie(resp, CSRF_COOKIE_NAME) is not None


def test_no_password_instance_keeps_a_stale_session_cookie_harmless(client, db, clock):
    _signed_in(client, db, clock, remembered=False)
    db.set_setting('app_password', '')
    clock[0] += 400 * 86400
    assert client.get('/api/v1/feeds').status_code == 200
