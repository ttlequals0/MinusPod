"""Only authenticated responses get a session or CSRF cookie.

A cross-site navigation arrives without the SameSite=Strict session cookie, so a fresh anonymous cookie would replace the real one. Public podcast-app endpoints also opt out to stay cacheable."""

import pytest

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('pubcookie_test_', secret_key='pubcookie-test-secret')

from main_app import app  # noqa: E402
from main_app.routes import PUBLIC_FEED_ENDPOINTS  # noqa: E402
from api.csrf import CSRF_COOKIE_NAME  # noqa: E402


@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c


def _cookie_names(response):
    return {h.split('=', 1)[0] for h in response.headers.getlist('Set-Cookie')}


def test_health_sets_no_cookies(client):
    response = client.get('/health')
    assert response.status_code == 200
    assert _cookie_names(response) == set()


def test_health_is_cacheable_past_the_cookie(client):
    # Vary: Cookie is what stops a CDN caching these at all.
    response = client.get('/health')
    assert 'Cookie' not in (response.headers.get('Vary') or '')


def test_a_missing_feed_still_sets_no_cookies(client):
    # The opt-out is keyed on the endpoint, so it holds on the error path too.
    response = client.get('/no-such-feed-here')
    assert _cookie_names(response) == set()


@pytest.mark.parametrize('path', [
    '/ui/',
    '/ui/feeds/example-podcast/episodes/a1b2c3d4e5f6',
    '/api/v1/auth/status',
])
def test_anonymous_responses_set_no_cookies(client, path):
    response = client.get(path)
    assert _cookie_names(response) & {'session', CSRF_COOKIE_NAME} == set()


@pytest.mark.parametrize('path', [
    '/api/v1/auth/status',
    '/ui/',
    '/ui/feeds/example-podcast/episodes/a1b2c3d4e5f6',
])
def test_authenticated_responses_get_the_csrf_cookie(path):
    # The frontend reads this cookie to populate X-CSRF-Token.
    authed = app.test_client()
    with authed.session_transaction() as sess:
        sess['authenticated'] = True
    response = authed.get(path)
    assert 'serve_ui' not in PUBLIC_FEED_ENDPOINTS
    assert CSRF_COOKIE_NAME in _cookie_names(response)


def test_every_listed_endpoint_is_a_real_route():
    # A typo here would silently stop applying the opt-out.
    registered = {rule.endpoint for rule in app.url_map.iter_rules()}
    assert PUBLIC_FEED_ENDPOINTS <= registered
