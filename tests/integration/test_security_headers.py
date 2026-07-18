"""Integration tests for the response-shape security improvements:
health liveness probe, X-Request-ID round-trip, baseline security headers.
"""

import pytest

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('sechdr_test_', secret_key='sechdr-test-secret')

import database
from main_app import app


@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c


def test_health_live_returns_ok(client):
    response = client.get('/api/v1/health/live')
    assert response.status_code == 200
    assert response.get_json() == {'status': 'ok'}


def test_health_live_no_auth_required(client):
    """The liveness probe must not require a session, regardless of
    whether a password is configured."""
    db = database.Database()
    db.set_setting('app_password', 'werkzeug$hash-placeholder')
    try:
        response = client.get('/api/v1/health/live')
        assert response.status_code == 200
    finally:
        db.set_setting('app_password', '')


def test_request_id_round_trip_generates_when_absent(client):
    response = client.get('/api/v1/auth/status')
    assert response.status_code == 200
    rid = response.headers.get('X-Request-ID')
    assert rid and len(rid) >= 8


def test_request_id_preserves_client_header(client):
    client_rid = 'req_client_supplied_abc123'
    response = client.get(
        '/api/v1/auth/status',
        headers={'X-Request-ID': client_rid},
    )
    assert response.headers.get('X-Request-ID') == client_rid


def test_request_id_truncates_over_128_chars(client):
    long_rid = 'x' * 200
    response = client.get(
        '/api/v1/auth/status',
        headers={'X-Request-ID': long_rid},
    )
    echoed = response.headers.get('X-Request-ID', '')
    assert len(echoed) <= 128


def test_security_headers_present_on_api(client):
    response = client.get('/api/v1/auth/status')
    assert response.headers.get('X-Content-Type-Options') == 'nosniff'
    assert response.headers.get('X-Frame-Options') == 'DENY'
    assert response.headers.get('Referrer-Policy') == 'strict-origin-when-cross-origin'


def test_hsts_not_enabled_by_default(client):
    response = client.get('/api/v1/auth/status')
    # MINUSPOD_ENABLE_HSTS defaults to false; the header must be absent.
    assert 'Strict-Transport-Security' not in response.headers


def test_csp_locked_down_on_json_responses(client):
    """JSON responses get a minimal CSP (default-src 'none'; frame-ancestors
    'none') so a JSON endpoint that ends up rendered in a frame or
    misinterpreted as HTML can't load anything."""
    response = client.get('/api/v1/auth/status')
    csp = response.headers.get('Content-Security-Policy', '')
    assert "default-src 'none'" in csp
    assert "frame-ancestors 'none'" in csp
