"""Integration tests for double-submit CSRF protection."""
import json
import os
import sys
import tempfile

import pytest

# Mirror the module-level DATA_DIR setup that other integration tests do,
# so importing main_app does not try to create /app/data. Done once at
# module import because main_app's Storage() runs at import time.
_test_data_dir = tempfile.mkdtemp(prefix='csrf_test_')
os.environ.setdefault('SECRET_KEY', 'csrf-test-secret')
os.environ.setdefault('DATA_DIR', _test_data_dir)
os.environ.setdefault('MINUSPOD_MASTER_PASSPHRASE', 'csrf-test-passphrase')

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

import database
import storage as storage_mod
database.Database._instance = None
database.Database.__init__.__defaults__ = (_test_data_dir,)
storage_mod.Storage.__init__.__defaults__ = (_test_data_dir,)


@pytest.fixture
def csrf_client():
    from main_app import app
    db = database.Database()
    db.set_setting('app_password', '')
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c


def test_get_receives_csrf_cookie(csrf_client):
    response = csrf_client.get('/api/v1/auth/status')
    assert response.status_code == 200
    cookie = csrf_client.get_cookie('minuspod_csrf')
    assert cookie is not None


def _get_csrf_token(csrf_client):
    csrf_client.get('/api/v1/auth/status')
    cookie = csrf_client.get_cookie('minuspod_csrf')
    assert cookie is not None
    return cookie.value


def _set_password(csrf_client, token, password='CsrfTestPw123!'):
    r = csrf_client.put(
        '/api/v1/auth/password',
        data=json.dumps({'newPassword': password}),
        content_type='application/json',
        headers={'X-CSRF-Token': token},
    )
    assert r.status_code == 200, r.get_data(as_text=True)


def test_post_without_csrf_token_rejected_after_auth(csrf_client):
    token = _get_csrf_token(csrf_client)
    _set_password(csrf_client, token)

    response = csrf_client.post(
        '/api/v1/feeds',
        data=json.dumps({'sourceUrl': 'https://example.com/feed.xml'}),
        content_type='application/json',
    )
    assert response.status_code == 403
    assert 'CSRF' in (response.get_data(as_text=True) or '')


def test_post_with_mismatched_csrf_rejected(csrf_client):
    token = _get_csrf_token(csrf_client)
    _set_password(csrf_client, token)

    response = csrf_client.post(
        '/api/v1/feeds',
        data=json.dumps({'sourceUrl': 'https://example.com/feed.xml'}),
        content_type='application/json',
        headers={'X-CSRF-Token': 'wrong-token'},
    )
    assert response.status_code == 403


def test_post_with_matching_csrf_token_passes_csrf_layer(csrf_client):
    token = _get_csrf_token(csrf_client)
    _set_password(csrf_client, token)

    cookie = csrf_client.get_cookie('minuspod_csrf')
    token = cookie.value

    response = csrf_client.post(
        '/api/v1/feeds',
        data=json.dumps({'sourceUrl': 'not-a-url'}),
        content_type='application/json',
        headers={'X-CSRF-Token': token},
    )
    assert response.status_code != 403


def test_login_endpoint_exempt_from_csrf(csrf_client):
    response = csrf_client.post(
        '/api/v1/auth/login',
        data=json.dumps({'password': 'wrong'}),
        content_type='application/json',
    )
    assert response.status_code in (200, 400, 401)


def test_safe_methods_do_not_require_csrf(csrf_client):
    response = csrf_client.get('/api/v1/auth/status')
    assert response.status_code == 200


def test_authenticated_session_without_csrf_token_is_rejected(csrf_client):
    """Simulate a stale pre-2.0 session cookie that has authenticated=True
    but no _csrf_token key. The CSRF layer must still 403, not pass through.

    Requires a configured app_password so the auth layer takes effect;
    without one, the server treats every request as authenticated and
    the CSRF check is moot by design.
    """
    from werkzeug.security import generate_password_hash
    db = database.Database()
    db.set_setting('app_password', generate_password_hash('CsrfStale123456', method='scrypt'))

    with csrf_client.session_transaction() as sess:
        sess['authenticated'] = True
        sess.pop('_csrf_token', None)

    response = csrf_client.post(
        '/api/v1/feeds',
        data=json.dumps({'sourceUrl': 'https://example.com/feed.xml'}),
        content_type='application/json',
    )
    assert response.status_code == 403
    assert 'CSRF' in (response.get_data(as_text=True) or '')
