from tests.app_bootstrap import bootstrap
_test_data_dir = bootstrap('login_remember_test_', secret_key='remember-test-secret')
import json
import pytest
from werkzeug.security import generate_password_hash
import database
from main_app import app
from api import limiter
from api.auth_state import SESSION_REMEMBERED_KEY, SESSION_LAST_USED_KEY
from api.csrf import CSRF_COOKIE_NAME
from tests.integration.openapi_conformance import assert_response_conforms

PW = 'correct-password-123456'


@pytest.fixture
def client():
    db = database.Database()
    db.set_setting('app_password', generate_password_hash(PW, method='scrypt'))
    app.config['TESTING'] = True
    limiter.enabled = False
    try:
        with app.test_client() as c:
            yield c
    finally:
        limiter.enabled = True
        db.set_setting('app_password', '')


def _login(client, body):
    return client.post('/api/v1/auth/login', data=json.dumps(body),
                       content_type='application/json',
                       environ_base={'REMOTE_ADDR': '192.168.1.5'})


def test_remember_true_sets_flag(client):
    assert _login(client, {'password': PW, 'rememberMe': True}).status_code == 200
    with client.session_transaction() as sess:
        assert sess[SESSION_REMEMBERED_KEY] is True
        assert isinstance(sess[SESSION_LAST_USED_KEY], float)


def test_remember_false_sets_flag_false(client):
    assert _login(client, {'password': PW, 'rememberMe': False}).status_code == 200
    with client.session_transaction() as sess:
        assert sess[SESSION_REMEMBERED_KEY] is False


def test_remember_omitted_defaults_false(client):
    assert _login(client, {'password': PW}).status_code == 200
    with client.session_transaction() as sess:
        assert sess[SESSION_REMEMBERED_KEY] is False


@pytest.mark.parametrize('value', ['true', 1, 0, None, [], {}])
def test_remember_non_boolean_rejected(client, value):
    r = _login(client, {'password': PW, 'rememberMe': value})
    assert_response_conforms('post', '/auth/login', r, status=400)
    with client.session_transaction() as sess:
        assert 'authenticated' not in sess


def test_wrong_password_with_remember_does_not_sign_in(client):
    assert _login(client, {'password': 'nope', 'rememberMe': True}).status_code == 401
    with client.session_transaction() as sess:
        assert SESSION_REMEMBERED_KEY not in sess


def test_relogin_without_remember_drops_the_flag(client):
    _login(client, {'password': PW, 'rememberMe': True})
    _login(client, {'password': PW})
    with client.session_transaction() as sess:
        assert sess[SESSION_REMEMBERED_KEY] is False


def test_password_change_keeps_remembered_state(client):
    _login(client, {'password': PW, 'rememberMe': True})
    with client.session_transaction() as sess:
        csrf = sess['_csrf_token']
    r = client.put('/api/v1/auth/password',
                   data=json.dumps({'currentPassword': PW, 'newPassword': 'another-strong-pw-1'}),
                   content_type='application/json',
                   headers={'X-CSRF-Token': csrf})
    assert r.status_code == 200
    with client.session_transaction() as sess:
        assert sess[SESSION_REMEMBERED_KEY] is True


def test_password_change_keeps_a_normal_session_normal(client):
    _login(client, {'password': PW})
    client.get('/api/v1/auth/status')
    with client.session_transaction() as sess:
        csrf = sess['_csrf_token']
    r = client.put('/api/v1/auth/password',
                   data=json.dumps({'currentPassword': PW, 'newPassword': 'another-strong-pw-1'}),
                   content_type='application/json',
                   headers={'X-CSRF-Token': csrf})
    assert r.status_code == 200
    with client.session_transaction() as sess:
        assert sess[SESSION_REMEMBERED_KEY] is False


def test_csrf_cookie_reissued_after_browser_restart_on_remembered_session(client):
    # A browser restart drops the expiry-less CSRF cookie and keeps the session cookie.
    _login(client, {'password': PW, 'rememberMe': True})
    client.delete_cookie(CSRF_COOKIE_NAME)
    r = client.get('/api/v1/auth/status')
    assert r.get_json()['authenticated'] is True
    assert any(c.startswith(f'{CSRF_COOKIE_NAME}=') for c in r.headers.getlist('Set-Cookie'))


@pytest.mark.parametrize('body', [['password'], 'password', {'password': None}, {'password': 5},
                                  {'rememberMe': True}, 7, None])
def test_malformed_login_body_is_a_400(client, body):
    r = _login(client, body)
    assert_response_conforms('post', '/auth/login', r, status=400)


def test_locked_client_with_a_bad_body_still_gets_429(client):
    ip = '8.8.8.8'
    db = database.Database()
    for _ in range(50):
        if db.record_auth_failure(ip):
            break
    r = client.post('/api/v1/auth/login', data=json.dumps({'password': PW, 'rememberMe': 'yes'}),
                    content_type='application/json', environ_base={'REMOTE_ADDR': ip})
    assert r.status_code == 429
    assert r.headers['Retry-After'].isdigit()
    r = client.post('/api/v1/auth/login', data=json.dumps(['password']),
                    content_type='application/json', environ_base={'REMOTE_ADDR': ip})
    assert r.status_code == 429
    db.record_auth_success(ip)
