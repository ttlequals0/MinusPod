from tests.app_bootstrap import bootstrap
_test_data_dir = bootstrap('session_expiry_test_', secret_key='expiry-test-secret')
import pytest
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone
from flask import session
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


def _pre_upgrade_cookie(hours_ago):
    """A session cookie signed hours_ago, without auth_last_used."""
    import time
    from itsdangerous.timed import TimestampSigner
    signed_at = int(time.time() - hours_ago * 3600)
    serializer = app.session_interface.get_signing_serializer(app)
    original = TimestampSigner.get_timestamp
    TimestampSigner.get_timestamp = lambda self: signed_at
    try:
        value = serializer.dumps({
            'authenticated': True,
            'auth_generation': auth_state.current_generation(Database()),
            '_permanent': True,
        })
    finally:
        TimestampSigner.get_timestamp = original
    return value, signed_at


def _client_with_cookie(value):
    c = app.test_client()
    c.set_cookie('session', value, domain='localhost')
    return c


def test_pre_upgrade_cookie_signed_inside_the_limit_keeps_working(db):
    value, signed_at = _pre_upgrade_cookie(hours_ago=1)
    c = _client_with_cookie(value)
    resp = c.get('/api/v1/feeds')
    assert resp.status_code == 200
    with c.session_transaction() as sess:
        assert sess['auth_last_used'] >= signed_at


def test_pre_upgrade_cookie_last_used_is_its_signing_time(db, monkeypatch):
    value, signed_at = _pre_upgrade_cookie(hours_ago=1)
    c = _client_with_cookie(value)
    seen = {}
    real = auth_state.check_session

    def spy(d):
        seen.setdefault('last_used', session.get('auth_last_used'))
        return real(d)
    monkeypatch.setattr(auth_state, 'check_session', spy)
    c.get('/api/v1/auth/status')
    assert seen['last_used'] == signed_at


def test_pre_upgrade_cookie_signed_past_the_limit_is_signed_out(db):
    value, _ = _pre_upgrade_cookie(hours_ago=25)
    c = _client_with_cookie(value)
    resp = c.get('/api/v1/feeds')
    assert resp.status_code == 401
    assert _set_cookie(resp, CSRF_COOKIE_NAME) is None
    cleared = _set_cookie(resp, 'session')
    assert cleared is not None and cleared.startswith('session=;')


def test_forged_future_last_used_cookie_is_signed_out(db):
    import time
    serializer = app.session_interface.get_signing_serializer(app)
    value = serializer.dumps({
        'authenticated': True, '_permanent': True,
        'auth_generation': auth_state.current_generation(db),
        'auth_last_used': time.time() + 10 * 86400})
    resp = _client_with_cookie(value).get('/api/v1/feeds')
    assert resp.status_code == 401


def _count_reads(monkeypatch):
    import config
    reads = []
    real = config._db_setting
    monkeypatch.setattr(config, '_db_setting', lambda key: (reads.append(key), real(key))[1])
    return reads


def test_one_authenticated_request_reads_each_session_setting_once(client, db, clock, monkeypatch):
    _signed_in(client, db, clock, remembered=False)
    reads = _count_reads(monkeypatch)
    assert client.get('/api/v1/settings').status_code == 200
    assert reads.count('session_lifetime_hours') == 1


def test_a_setting_changed_between_requests_applies_on_the_second(client, db, clock, monkeypatch):
    _signed_in(client, db, clock, remembered=False)
    reads = _count_reads(monkeypatch)
    first = client.get('/api/v1/auth/status')
    db.set_setting('session_lifetime_hours', '2', is_default=False)
    second = client.get('/api/v1/auth/status')
    assert reads.count('session_lifetime_hours') == 2
    assert _cookie_lifetime_seconds(_set_cookie(first, 'session')) > 20 * 3600
    assert _cookie_lifetime_seconds(_set_cookie(second, 'session')) < 3 * 3600


def test_saving_the_session_length_sets_the_cookie_expiry_in_the_same_response(client, db, clock):
    db.set_setting('session_lifetime_hours', '5', is_default=False)
    _signed_in(client, db, clock, remembered=False)
    client.get('/api/v1/auth/status')
    csrf = next(c.value for c in client._cookies.values() if c.key == CSRF_COOKIE_NAME)
    resp = client.put('/api/v1/settings/ad-detection', json={'sessionLifetimeHours': 9},
                      headers={'X-CSRF-Token': csrf})
    assert resp.status_code == 200
    lifetime = _cookie_lifetime_seconds(_set_cookie(resp, 'session'))
    assert abs(lifetime - 9 * 3600) < 60


def _count_checks(monkeypatch):
    calls = []
    real = auth_state.check_session
    monkeypatch.setattr(auth_state, 'check_session', lambda d: (calls.append(1), real(d))[1])
    return calls


def _csrf_token(client):
    client.get('/api/v1/auth/status')
    return client.get_cookie(CSRF_COOKIE_NAME).value


@pytest.mark.parametrize('method,path', [
    ('get', '/api/v1/feeds'), ('get', '/api/v1/auth/status'), ('get', '/ui/'),
    ('post', '/api/v1/feeds')])
def test_the_session_check_runs_once_per_request(client, db, clock, monkeypatch, method, path):
    _signed_in(client, db, clock, remembered=False)
    token = _csrf_token(client)
    calls = _count_checks(monkeypatch)
    resp = getattr(client, method)(path, json={} if method == 'post' else None,
                                   headers={'X-CSRF-Token': token})
    assert resp.status_code not in (401, 403)
    assert len(calls) == 1


def test_one_authenticated_request_reads_each_auth_setting_once(client, db, clock, monkeypatch):
    _signed_in(client, db, clock, remembered=False)
    reads = []
    real = Database.get_setting
    monkeypatch.setattr(Database, 'get_setting',
                        lambda self, key: (reads.append(key), real(self, key))[1])
    assert client.get('/api/v1/feeds').status_code == 200
    assert reads.count('app_password') == 1
    assert reads.count(auth_state.AUTH_GENERATION_KEY) == 1


def test_login_response_cookie_matches_the_remembered_limit(client, db):
    resp = client.post('/api/v1/auth/login', json={'password': PW, 'rememberMe': True})
    assert resp.status_code == 200
    assert abs(_cookie_lifetime_seconds(_set_cookie(resp, 'session')) - 30 * 86400) < 60
    assert _set_cookie(resp, CSRF_COOKIE_NAME) is not None


def test_relogin_without_remember_shortens_the_cookie_in_the_same_response(client, db):
    client.post('/api/v1/auth/login', json={'password': PW, 'rememberMe': True})
    resp = client.post('/api/v1/auth/login', json={'password': PW})
    assert abs(_cookie_lifetime_seconds(_set_cookie(resp, 'session')) - 24 * 3600) < 60


def test_first_password_response_signs_in_with_the_normal_limit(client, db):
    db.set_setting('app_password', '')
    resp = client.put('/api/v1/auth/password', json={'newPassword': PW})
    assert resp.status_code == 200
    assert abs(_cookie_lifetime_seconds(_set_cookie(resp, 'session')) - 24 * 3600) < 60
    assert _set_cookie(resp, CSRF_COOKIE_NAME) is not None
    assert client.get('/api/v1/auth/status').get_json() == {
        'passwordSet': True, 'authenticated': True}


def test_removing_the_password_response_sets_no_csrf_cookie(client, db):
    client.post('/api/v1/auth/login', json={'password': PW})
    token = client.get_cookie(CSRF_COOKIE_NAME).value
    resp = client.put('/api/v1/auth/password', json={'currentPassword': PW, 'newPassword': ''},
                      headers={'X-CSRF-Token': token})
    assert resp.status_code == 200
    assert _set_cookie(resp, CSRF_COOKIE_NAME) is None
    cleared = _set_cookie(resp, 'session')
    assert cleared is not None and cleared.startswith('session=;')


@pytest.mark.parametrize('path', ['/api/v1/auth/status', '/api/v1/feeds', '/ui/'])
def test_revoked_session_is_cleared_without_a_csrf_cookie(client, db, clock, path):
    _signed_in(client, db, clock, remembered=True)
    db.increment_setting_int(auth_state.AUTH_GENERATION_KEY)
    resp = client.get(path)
    assert _set_cookie(resp, CSRF_COOKIE_NAME) is None
    cleared = _set_cookie(resp, 'session')
    assert cleared is not None and cleared.startswith('session=;')


def test_expired_session_stream_fails_at_connect(client, db, clock):
    _signed_in(client, db, clock, remembered=False)
    clock[0] += 24 * 3600 + 1
    response = client.get('/api/v1/status/stream', buffered=False)
    assert b'event: auth-failed' in next(iter(response.response))
