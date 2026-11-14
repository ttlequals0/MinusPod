from tests.app_bootstrap import bootstrap
_test_data_dir = bootstrap('session_revoke_test_', secret_key='revoke-test-secret')
import json
import pytest
from werkzeug.security import generate_password_hash
import database
from main_app import app
from api import limiter
from api.csrf import CSRF_COOKIE_NAME, CSRF_HEADER_NAME

PW = 'correct-password-123456'
NEW_PW = 'another-password-7890123'
ADDR = {'REMOTE_ADDR': '192.168.1.5'}


@pytest.fixture
def db():
    d = database.Database()
    d.set_setting('app_password', generate_password_hash(PW, method='scrypt'))
    yield d
    d.set_setting('app_password', '')


@pytest.fixture
def clients(db):
    app.config['TESTING'] = True
    limiter.enabled = False
    try:
        yield app.test_client(), app.test_client()
    finally:
        limiter.enabled = True


def _login(client, password=PW, remember=True):
    return client.post('/api/v1/auth/login', data=json.dumps({'password': password, 'rememberMe': remember}),
                       content_type='application/json', environ_base=ADDR)


def _set_cookies(resp):
    return {h.split('=', 1)[0]: h for h in resp.headers.getlist('Set-Cookie')}


def test_revoked_session_401_deletes_the_cookie_and_sets_no_csrf(clients):
    first, second = clients
    assert _login(first).status_code == 200
    assert _login(second).status_code == 200
    token = second.get_cookie(CSRF_COOKIE_NAME).value
    changed = second.put('/api/v1/auth/password', environ_base=ADDR, headers={CSRF_HEADER_NAME: token},
                         data=json.dumps({'currentPassword': PW, 'newPassword': NEW_PW}),
                         content_type='application/json')
    assert changed.status_code == 200

    resp = first.get('/api/v1/settings', environ_base=ADDR)
    assert resp.status_code == 401
    cookies = _set_cookies(resp)
    assert 'session' in cookies and ('Max-Age=0' in cookies['session'] or '1970' in cookies['session'])
    assert CSRF_COOKIE_NAME not in cookies
    assert first.get('/api/v1/auth/status', environ_base=ADDR).get_json()['authenticated'] is False
