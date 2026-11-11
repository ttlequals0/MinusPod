"""Authenticated cookies are revoked when the shared password changes."""

import pytest

from tests.app_bootstrap import bootstrap


_test_data_dir = bootstrap('session_revocation_test_', secret_key='session-revocation-secret')

import database
from api import limiter
from main_app import app


PASSWORD = 'OriginalPassword123!'
NEW_PASSWORD = 'ReplacementPassword123!'


@pytest.fixture(autouse=True)
def auth_state():
    from werkzeug.security import generate_password_hash

    db = database.Database()
    db.set_setting('app_password', generate_password_hash(PASSWORD, method='scrypt'))
    db.set_setting('auth_session_generation', '0')
    app.config['TESTING'] = True
    limiter.enabled = False
    limiter.reset()
    try:
        yield
    finally:
        limiter.enabled = True


def _login(client, password=PASSWORD):
    response = client.post('/api/v1/auth/login', json={'password': password})
    assert response.status_code == 200


def _csrf(client):
    client.get('/api/v1/auth/status')
    return client.get_cookie('minuspod_csrf').value


def test_password_change_revokes_other_authenticated_cookie():
    first = app.test_client()
    second = app.test_client()
    _login(first)
    _login(second)

    response = first.put(
        '/api/v1/auth/password',
        json={'currentPassword': PASSWORD, 'newPassword': NEW_PASSWORD},
        headers={'X-CSRF-Token': _csrf(first)},
    )

    assert response.status_code == 200
    assert first.get('/api/v1/feeds').status_code == 200
    assert second.get('/api/v1/feeds').status_code == 401


def test_legacy_boolean_only_cookie_is_rejected():
    client = app.test_client()
    with client.session_transaction() as session:
        session['authenticated'] = True

    response = client.get('/api/v1/feeds')

    assert response.status_code == 401


def test_required_auth_blocks_remote_passwordless_api(monkeypatch):
    db = database.Database()
    db.set_setting('app_password', '')
    monkeypatch.setenv('MINUSPOD_REQUIRE_AUTH', 'true')
    client = app.test_client()

    blocked = client.put(
        '/api/v1/auth/password',
        json={'newPassword': NEW_PASSWORD},
        environ_base={'REMOTE_ADDR': '203.0.113.10'},
    )

    assert blocked.status_code == 403
    assert db.get_setting('app_password') == ''


def test_setup_token_allows_remote_first_password(monkeypatch):
    db = database.Database()
    db.set_setting('app_password', '')
    monkeypatch.setenv('MINUSPOD_REQUIRE_AUTH', 'true')
    monkeypatch.setenv('MINUSPOD_SETUP_TOKEN', 'one-time-setup-token')
    client = app.test_client()

    response = client.put(
        '/api/v1/auth/password',
        json={'newPassword': NEW_PASSWORD},
        headers={'X-MinusPod-Setup-Token': 'one-time-setup-token'},
        environ_base={'REMOTE_ADDR': '203.0.113.10'},
    )

    assert response.status_code == 200


def test_required_auth_reports_passwordless_instance_as_locked(monkeypatch):
    db = database.Database()
    db.set_setting('app_password', '')
    monkeypatch.setenv('MINUSPOD_REQUIRE_AUTH', 'true')
    client = app.test_client()

    status = client.get('/api/v1/auth/status').get_json()

    assert status == {'passwordSet': False, 'authenticated': False}
    assert client.post('/api/v1/auth/login', json={'password': ''}).status_code == 503


def test_required_auth_passwordless_stream_fails_closed(monkeypatch):
    db = database.Database()
    db.set_setting('app_password', '')
    monkeypatch.setenv('MINUSPOD_REQUIRE_AUTH', 'true')
    client = app.test_client()

    response = client.get('/api/v1/status/stream', buffered=False)

    assert b'event: auth-failed' in next(response.response)


def test_setup_token_rejects_non_ascii_without_server_error(monkeypatch):
    db = database.Database()
    db.set_setting('app_password', '')
    monkeypatch.setenv('MINUSPOD_REQUIRE_AUTH', 'true')
    monkeypatch.setenv('MINUSPOD_SETUP_TOKEN', 'ascii-token')
    client = app.test_client()

    response = client.put(
        '/api/v1/auth/password',
        json={'newPassword': NEW_PASSWORD},
        headers={'X-MinusPod-Setup-Token': 'snowman-\u2603'},
        environ_base={'REMOTE_ADDR': '203.0.113.10'},
    )

    assert response.status_code == 403


def _cookie_names(response):
    return {h.split('=', 1)[0] for h in response.headers.getlist('Set-Cookie')}


def test_cookieless_navigation_does_not_replace_the_session():
    # A cross-site navigation drops the SameSite=Strict cookie; the reply must not mint a new one.
    operator = app.test_client()
    _login(operator)
    stranger = app.test_client()

    nav = stranger.get('/ui/feeds/example-podcast/episodes/a1b2c3d4e5f6')

    assert 'session' not in _cookie_names(nav)
    assert operator.get('/api/v1/auth/status').get_json()['authenticated'] is True


def test_login_sets_csrf_cookie_and_mutation_passes():
    client = app.test_client()
    response = client.post('/api/v1/auth/login', json={'password': PASSWORD})
    assert {'session', 'minuspod_csrf'} <= _cookie_names(response)

    token = client.get_cookie('minuspod_csrf').value
    ok = client.post('/api/v1/feeds', json={'sourceUrl': 'not-a-url'}, headers={'X-CSRF-Token': token})
    assert ok.status_code != 403
    bad = client.post('/api/v1/feeds', json={'sourceUrl': 'not-a-url'})
    assert bad.status_code == 403


def test_logout_does_not_mint_an_anonymous_session():
    client = app.test_client()
    _login(client)
    token = client.get_cookie('minuspod_csrf').value

    response = client.post('/api/v1/auth/logout', headers={'X-CSRF-Token': token})

    assert response.status_code == 200
    cookies = response.headers.getlist('Set-Cookie')
    assert not any(c.startswith('session=') and not c.startswith('session=;') for c in cookies)
    assert client.get('/api/v1/auth/status').get_json()['authenticated'] is False
