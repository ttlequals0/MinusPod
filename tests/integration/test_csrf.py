"""Integration tests for double-submit CSRF protection."""
import json

import pytest

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('csrf_test_', secret_key='csrf-test-secret', passphrase='csrf-test-passphrase')

import database
from api.auth_state import SESSION_GENERATION_KEY, current_generation


@pytest.fixture
def csrf_client():
    from main_app import app
    db = database.Database()
    db.set_setting('app_password', '')
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c


def test_anonymous_get_receives_no_csrf_cookie(csrf_client):
    response = csrf_client.get('/api/v1/auth/status')
    assert response.status_code == 200
    assert csrf_client.get_cookie('minuspod_csrf') is None


def test_setting_the_first_password_receives_csrf_cookie(csrf_client):
    _set_password(csrf_client)
    assert csrf_client.get_cookie('minuspod_csrf') is not None


def _get_csrf_token(csrf_client):
    # First-run setup logs the client in and mints the cookie on that response.
    _set_password(csrf_client)
    cookie = csrf_client.get_cookie('minuspod_csrf')
    assert cookie is not None
    return cookie.value


def _set_password(csrf_client, password='CsrfTestPw123!'):
    r = csrf_client.put(
        '/api/v1/auth/password',
        data=json.dumps({'newPassword': password}),
        content_type='application/json',
    )
    assert r.status_code == 200, r.get_data(as_text=True)


def test_post_without_csrf_token_rejected_after_auth(csrf_client):
    _get_csrf_token(csrf_client)

    response = csrf_client.post(
        '/api/v1/feeds',
        data=json.dumps({'sourceUrl': 'https://example.com/feed.xml'}),
        content_type='application/json',
    )
    assert response.status_code == 403
    assert 'CSRF' in (response.get_data(as_text=True) or '')


def test_opml_post_without_csrf_token_rejected_after_auth(csrf_client):
    _get_csrf_token(csrf_client)

    response = csrf_client.post(
        '/api/v1/feeds/export-opml',
        json={'slugs': ['example-feed']},
    )
    assert response.status_code == 403
    assert 'CSRF' in (response.get_data(as_text=True) or '')


def test_post_with_mismatched_csrf_rejected(csrf_client):
    _get_csrf_token(csrf_client)

    response = csrf_client.post(
        '/api/v1/feeds',
        data=json.dumps({'sourceUrl': 'https://example.com/feed.xml'}),
        content_type='application/json',
        headers={'X-CSRF-Token': 'wrong-token'},
    )
    assert response.status_code == 403


def test_post_with_matching_csrf_token_passes_csrf_layer(csrf_client):
    token = _get_csrf_token(csrf_client)

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
    """A stale pre-2.0 session cookie (authenticated=True, no _csrf_token) must still 403.

    Needs a configured app_password; without one every request counts as authenticated
    and the CSRF check is moot by design.
    """
    from werkzeug.security import generate_password_hash
    db = database.Database()
    db.set_setting('app_password', generate_password_hash('CsrfStale123456', method='scrypt'))

    with csrf_client.session_transaction() as sess:
        sess['authenticated'] = True
        sess[SESSION_GENERATION_KEY] = current_generation(db)
        sess.pop('_csrf_token', None)

    response = csrf_client.post(
        '/api/v1/feeds',
        data=json.dumps({'sourceUrl': 'https://example.com/feed.xml'}),
        content_type='application/json',
    )
    assert response.status_code == 403
    assert 'CSRF' in (response.get_data(as_text=True) or '')
