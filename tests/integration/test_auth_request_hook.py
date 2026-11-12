"""The before_request session check: exempt endpoints, failures and the requests that rely on it."""
from tests.app_bootstrap import bootstrap
_test_data_dir = bootstrap('auth_hook_test_', secret_key='auth-hook-secret')
import logging
import sqlite3
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from flask import Flask
from werkzeug.security import generate_password_hash

import api.auth_state as auth_state
import api.status as status_mod
import config_transfer
from api import api as api_blueprint, init_limiter, limiter
from api.csrf import CSRF_COOKIE_NAME
from database import Database
from main_app import app
from main_app.routes import PUBLIC_FEED_ENDPOINTS

PW = 'correct-password-123456'
HOOK_MISSING_LOG = 'before the request hook stored it'


def _set_cookie(resp, name):
    return next((h for h in resp.headers.getlist('Set-Cookie') if h.startswith(f'{name}=')), None)


def _lifetime_seconds(header):
    expires = next(p.split('=', 1)[1] for p in header.split(';') if p.strip().lower().startswith('expires='))
    return (parsedate_to_datetime(expires) - datetime.now(timezone.utc)).total_seconds()


@pytest.fixture
def db():
    d = Database()
    d.set_setting('app_password', generate_password_hash(PW, method='scrypt'))
    d.set_setting('session_lifetime_hours', '', is_default=True)
    d.set_setting('session_remember_days', '', is_default=True)
    app.config['TESTING'] = True
    limiter.enabled = False
    yield d
    limiter.enabled = True
    d.set_setting('app_password', '')


@pytest.fixture
def clock(monkeypatch):
    t = [1_000_000.0]
    monkeypatch.setattr(auth_state, '_now', lambda: t[0])
    return t


@pytest.fixture
def client(db):
    with app.test_client() as c:
        yield c


def _signed_in(client, db, clock):
    with client.session_transaction() as sess:
        sess.permanent = True
        sess['authenticated'] = True
        sess['auth_generation'] = auth_state.current_generation(db)
        sess['auth_remembered'] = False
        sess['auth_last_used'] = clock[0]


def _errors(caplog):
    return [r for r in caplog.records if r.levelno >= logging.ERROR]


@pytest.fixture
def db_down(monkeypatch):
    def boom(*_a, **_k):
        raise sqlite3.OperationalError('disk I/O error')
    monkeypatch.setattr(Database, 'get_setting', boom)


def test_liveness_answers_while_the_settings_read_fails(client, db_down):
    resp = client.get('/api/v1/health/live')
    assert (resp.status_code, resp.get_json()) == (200, {'status': 'ok'})
    assert resp.headers.getlist('Set-Cookie') == []


def test_readiness_keeps_its_shape_while_the_settings_read_fails(client, db_down):
    resp = client.get('/api/v1/health')
    assert resp.status_code in (200, 503)
    assert set(resp.get_json()) == {'status', 'checks', 'version'}
    assert resp.headers.getlist('Set-Cookie') == []


@pytest.mark.parametrize('path', ['/api/v1/health', '/api/v1/health/live'])
def test_signed_in_health_poll_sets_no_cookie_and_logs_no_error(client, db, clock, caplog, path):
    _signed_in(client, db, clock)
    with caplog.at_level(logging.ERROR):
        resp = client.get(path)
    assert resp.headers.getlist('Set-Cookie') == []
    assert not any(HOOK_MISSING_LOG in r.getMessage() for r in caplog.records)


def test_hook_exempt_endpoints_are_only_the_health_probes():
    allowed = {'api.health_live', 'api.health_check'}
    assert auth_state.AUTH_HOOK_EXEMPT_ENDPOINTS == allowed
    endpoints = {rule.endpoint for rule in app.url_map.iter_rules()}
    assert auth_state.AUTH_HOOK_EXEMPT_ENDPOINTS <= endpoints
    assert PUBLIC_FEED_ENDPOINTS <= endpoints
    skipped = (PUBLIC_FEED_ENDPOINTS | auth_state.AUTH_HOOK_EXEMPT_ENDPOINTS)
    assert {e for e in skipped if e.startswith('api.')} == allowed


def test_a_failing_hook_answers_500_without_running_the_view(client, db, clock, monkeypatch, caplog):
    _signed_in(client, db, clock)

    def boom(_db):
        raise sqlite3.OperationalError('database is locked')
    ran = []
    monkeypatch.setattr(auth_state, 'check_session', boom)
    monkeypatch.setitem(app.view_functions, 'api.auth_status', lambda: ran.append(1) or 'ran')
    with caplog.at_level(logging.ERROR):
        resp = client.get('/api/v1/auth/status')
    assert resp.status_code == 500 and resp.get_json()['status'] == 500
    assert ran == []
    assert resp.headers.getlist('Set-Cookie') == []
    assert resp.headers.get('X-Request-ID')
    errors = _errors(caplog)
    assert len(errors) == 1 and 'OperationalError' in errors[0].getMessage()


def test_a_failing_lazy_check_on_a_page_keeps_the_page(client, db, clock, monkeypatch, caplog):
    normal = client.get('/ui/')
    _signed_in(client, db, clock)

    def boom(_db):
        raise sqlite3.OperationalError('database is locked')
    monkeypatch.setattr(auth_state, 'check_session', boom)
    with caplog.at_level(logging.ERROR):
        resp = client.get('/ui/')
    assert (resp.status_code, resp.data) == (normal.status_code, normal.data)
    assert resp.headers.getlist('Set-Cookie') == []
    errors = _errors(caplog)
    assert len(errors) == 1 and 'OperationalError' in errors[0].getMessage()


def _count_reads(monkeypatch):
    reads = []
    real_get = Database.get_setting
    monkeypatch.setattr(Database, 'get_setting', lambda self, key: (reads.append(key), real_get(self, key))[1])
    return reads


ASSET = '/ui/assets/' + next(p.name for p in (Path(__file__).resolve().parents[2] / 'static/ui/assets').iterdir())


@pytest.mark.parametrize('path', ['/ui/', ASSET, '/no/such/page'])
def test_anonymous_page_requests_read_no_settings(client, monkeypatch, path):
    reads = _count_reads(monkeypatch)
    resp = client.get(path)
    assert reads == []
    assert resp.headers.getlist('Set-Cookie') == []


@pytest.mark.parametrize('path', ['/ui/', ASSET])
def test_anonymous_page_requests_survive_a_database_failure(client, db_down, path):
    resp = client.get(path)
    assert resp.status_code == 200
    assert resp.headers.getlist('Set-Cookie') == []


def test_signed_in_page_gets_a_csrf_cookie_with_one_read_per_setting(client, db, clock, monkeypatch):
    _signed_in(client, db, clock)
    reads = _count_reads(monkeypatch)
    resp = client.get('/ui/')
    assert _set_cookie(resp, CSRF_COOKIE_NAME) is not None
    assert sorted(reads) == sorted(['app_password', auth_state.AUTH_GENERATION_KEY,
                                    'session_lifetime_hours'])


def test_no_password_anonymous_status_is_authenticated(client, db):
    db.set_setting('app_password', '')
    assert client.get('/api/v1/auth/status').get_json() == {'passwordSet': False, 'authenticated': True}


@pytest.fixture
def hookless_client(db):
    bare = Flask(__name__)
    bare.secret_key = 'hookless-secret'
    bare.register_blueprint(api_blueprint)
    init_limiter(bare)
    limiter.enabled = False
    return bare.test_client()


def test_missing_auth_state_is_a_500_not_a_signed_out_state(hookless_client, caplog):
    with caplog.at_level(logging.ERROR):
        resp = hookless_client.get('/api/v1/feeds')
    assert (resp.status_code, resp.get_json()) == (500, {'error': 'Internal server error', 'status': 500})
    assert resp.headers.getlist('Set-Cookie') == []
    assert len(_errors(caplog)) == 1


def test_setup_token_gate_reads_the_password_itself(hookless_client, db, monkeypatch):
    db.set_setting('app_password', '')
    monkeypatch.setenv('MINUSPOD_REQUIRE_AUTH', 'true')
    resp = hookless_client.put('/api/v1/auth/password', json={'newPassword': PW},
                               environ_base={'REMOTE_ADDR': '203.0.113.10'})
    assert resp.status_code == 403
    assert not db.get_setting('app_password')


def test_config_import_response_uses_the_new_session_length(client, db, clock):
    db.set_setting('session_lifetime_hours', '24', is_default=True)
    db.set_setting('session_remember_days', '30', is_default=True)
    _signed_in(client, db, clock)
    client.get('/api/v1/auth/status')
    hdr = {'X-CSRF-Token': client.get_cookie(CSRF_COOKIE_NAME).value}
    document = config_transfer.export_config(db, '2.98.7')
    document['settings'] = {'session_lifetime_hours': 9}
    preview = client.post('/api/v1/system/config-import/preview',
                          json={'document': document, 'scope': 'global'}, headers=hdr)
    assert preview.status_code == 200, preview.get_json()
    resp = client.post('/api/v1/system/config-import', headers=hdr, json={
        'document': document, 'scope': 'global', 'previewToken': preview.get_json()['previewToken']})
    assert resp.status_code == 200, resp.get_json()
    assert abs(_lifetime_seconds(_set_cookie(resp, 'session')) - 9 * 3600) < 60


@pytest.fixture
def stream_time(monkeypatch, clock):
    """Fake time for the stream: each poll sleep also moves the auth clock by a day."""
    t = [0.0]

    def sleep(seconds):
        t[0] += seconds
        clock[0] += 25 * 3600
    monkeypatch.setattr(status_mod, 'time', SimpleNamespace(monotonic=lambda: t[0], sleep=sleep))
    monkeypatch.setattr(status_mod, 'status_payload', lambda: {'revision': 1})


def test_stream_checks_only_revocation_per_event_and_stops_at_30_seconds(client, db, clock, stream_time):
    _signed_in(client, db, clock)
    chunks = list(client.get('/api/v1/status/stream', buffered=False).response)
    assert status_mod._SSE_MAX_SECONDS == 30
    assert len(chunks) == 30 // status_mod._SSE_POLL_SECONDS
    assert chunks[0].startswith(b'data:')
    assert all(c.startswith(b': keepalive') for c in chunks[1:])


def test_stream_ends_on_revocation(client, db, clock, stream_time):
    _signed_in(client, db, clock)
    chunks = iter(client.get('/api/v1/status/stream', buffered=False).response)
    assert next(chunks).startswith(b'data:')
    db.increment_setting_int(auth_state.AUTH_GENERATION_KEY)
    assert next(chunks).startswith(b'event: auth-failed')


def test_blank_stored_session_lengths_export_report_the_default_and_log_in(client, db, clock):
    db.set_setting('session_lifetime_hours', '', is_default=False)
    db.set_setting('session_remember_days', '  ', is_default=False)
    exported = config_transfer.export_config(db, '2.98.7')['settings']
    assert (exported['session_lifetime_hours'], exported['session_remember_days']) == (24, 30)
    _signed_in(client, db, clock)
    body = client.get('/api/v1/settings').get_json()
    assert (body['sessionLifetimeHours']['value'], body['sessionRememberDays']['value']) == (24, 30)
    client.post('/api/v1/auth/logout')
    resp = client.post('/api/v1/auth/login', json={'password': PW},
                       environ_base={'REMOTE_ADDR': '203.0.113.10'})
    assert resp.status_code == 200, resp.get_json()
    assert abs(_lifetime_seconds(_set_cookie(resp, 'session')) - 24 * 3600) < 60


def test_anonymous_request_inside_a_signed_in_context_is_401(client, db, clock):
    # client's preserved context keeps the app context, and with it g, alive.
    _signed_in(client, db, clock)
    assert client.get('/api/v1/auth/status').get_json()['authenticated'] is True
    with app.test_client() as anonymous:
        resp = anonymous.post('/api/v1/feeds', json={})
        assert resp.status_code == 401
        assert _set_cookie(resp, CSRF_COOKIE_NAME) is None
        assert anonymous.get('/api/v1/auth/status').get_json()['authenticated'] is False


def test_signed_in_request_inside_an_anonymous_context_works(client, db, clock):
    assert client.get('/api/v1/auth/status').get_json()['authenticated'] is False
    with app.test_client() as signed:
        _signed_in(signed, db, clock)
        resp = signed.get('/api/v1/auth/status')
        assert resp.get_json()['authenticated'] is True
        assert _set_cookie(resp, CSRF_COOKIE_NAME) is not None


@pytest.mark.parametrize('exempt', ['/api/v1/health/live', '/example-podcast'])
def test_an_exempt_request_carries_no_auth_state(client, db, clock, exempt):
    _signed_in(client, db, clock)
    assert client.get('/api/v1/auth/status').get_json()['authenticated'] is True
    with app.test_client() as anonymous:
        anonymous.get(exempt)
        assert auth_state.stored_auth() is None
        assert anonymous.post('/api/v1/feeds', json={}).status_code == 401


def test_a_state_from_another_request_is_never_trusted(client, db, clock):
    _signed_in(client, db, clock)
    client.get('/api/v1/auth/status')
    with app.test_request_context('/api/v1/feeds'):
        auth_state.refresh_auth(state=auth_state.AuthState(True, True, True, True))
    with app.test_request_context('/api/v1/feeds'):
        assert auth_state.stored_auth() is None
        assert auth_state.current_auth().signed_in is False


def _raise_on_second_check(monkeypatch):
    real = auth_state.check_session
    calls = []

    def flaky(d):
        calls.append(1)
        if len(calls) >= 2:
            raise sqlite3.OperationalError('database is locked')
        return real(d)
    monkeypatch.setattr(auth_state, 'check_session', flaky)


def _assert_signed_out_response(resp):
    assert resp.status_code == 500
    assert _set_cookie(resp, CSRF_COOKIE_NAME) is None
    cleared = _set_cookie(resp, 'session')
    assert cleared is not None and cleared.startswith('session=;')


def test_failed_logout_recheck_leaves_the_response_signed_out(client, db, monkeypatch):
    client.post('/api/v1/auth/login', json={'password': PW})
    token = client.get_cookie(CSRF_COOKIE_NAME).value
    _raise_on_second_check(monkeypatch)
    resp = client.post('/api/v1/auth/logout', headers={'X-CSRF-Token': token})
    _assert_signed_out_response(resp)


def test_failed_password_removal_recheck_leaves_the_response_signed_out(client, db, monkeypatch):
    client.post('/api/v1/auth/login', json={'password': PW})
    token = client.get_cookie(CSRF_COOKIE_NAME).value
    _raise_on_second_check(monkeypatch)
    resp = client.put('/api/v1/auth/password', json={'currentPassword': PW, 'newPassword': ''},
                      headers={'X-CSRF-Token': token})
    _assert_signed_out_response(resp)


def test_failed_session_length_recheck_writes_no_cookie(client, db, clock, monkeypatch):
    _signed_in(client, db, clock)
    client.get('/api/v1/auth/status')
    token = client.get_cookie(CSRF_COOKIE_NAME).value
    _raise_on_second_check(monkeypatch)
    resp = client.put('/api/v1/settings/ad-detection', json={'sessionLifetimeHours': 2},
                      headers={'X-CSRF-Token': token})
    assert resp.status_code == 500
    assert resp.headers.getlist('Set-Cookie') == []


@pytest.mark.parametrize('expired', [False, True])
def test_a_slug_guard_400_inside_a_signed_in_context_sets_no_cookie(client, db, clock, expired):
    _signed_in(client, db, clock)
    assert client.get('/api/v1/auth/status').get_json()['authenticated'] is True
    with app.test_client() as other:
        if expired:
            _signed_in(other, db, clock)
            clock[0] += 25 * 3600
        resp = other.get('/api/v1/feeds/example-podcast/episodes/zzz')
        assert resp.status_code == 400
        assert resp.headers.getlist('Set-Cookie') == []


@pytest.mark.parametrize('shape', [
    {'auth_generation': 0, 'auth_last_used': 1_000_000.0},
    {'authenticated': False, 'auth_generation': 0, 'auth_last_used': 1_000_000.0},
])
@pytest.mark.parametrize('path', ['/ui/', '/api/v1/auth/status'])
def test_auth_keys_without_a_sign_in_delete_the_cookie(client, db, clock, shape, path):
    with client.session_transaction() as sess:
        sess.permanent = True
        sess.update(shape)
    resp = client.get(path)
    cleared = _set_cookie(resp, 'session')
    assert cleared is not None and cleared.startswith('session=;')
    assert _set_cookie(resp, CSRF_COOKIE_NAME) is None
    assert client.get(path).headers.getlist('Set-Cookie') == []


def test_no_password_mode_leaves_auth_keys_without_a_sign_in(client, db, clock):
    db.set_setting('app_password', '')
    with client.session_transaction() as sess:
        sess.permanent = True
        sess.update({'authenticated': False, 'auth_last_used': 1_000_000.0})
    assert client.get('/api/v1/auth/status').get_json()['authenticated'] is True
    with client.session_transaction() as sess:
        assert sess['auth_last_used'] == 1_000_000.0
