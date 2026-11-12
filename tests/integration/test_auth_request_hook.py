"""The before_request session check: exempt endpoints, failures and the requests that rely on it."""
from tests.app_bootstrap import bootstrap
_test_data_dir = bootstrap('auth_hook_test_', secret_key='auth-hook-secret')
import logging
import sqlite3
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
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


def test_a_failing_hook_on_a_page_answers_500(client, db, clock, monkeypatch):
    _signed_in(client, db, clock)
    monkeypatch.setattr(auth_state, 'check_session', lambda _db: 1 / 0)
    resp = client.get('/ui/')
    assert resp.status_code == 500
    assert resp.headers.getlist('Set-Cookie') == []


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
