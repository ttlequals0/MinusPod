from tests.app_bootstrap import bootstrap
_test_data_dir = bootstrap('login_retry_test_', secret_key='retry-test-secret')
import json
import pytest
from werkzeug.security import generate_password_hash
import database
from main_app import app
from api import limiter
from database.auth_lockout import LOCKOUT_THRESHOLD
from tests.integration.openapi_conformance import assert_response_conforms

PW = 'correct-password-123456'
PRIVATE_IP = '192.168.1.9'


@pytest.fixture
def db():
    d = database.Database()
    conn = d.get_connection()
    conn.execute("DELETE FROM auth_failures")
    conn.commit()
    d.set_setting('app_password', generate_password_hash(PW, method='scrypt'))
    app.config['TESTING'] = True
    limiter.reset()
    yield d
    limiter.reset()
    limiter.enabled = True
    d.set_setting('app_password', '')


@pytest.fixture
def client(db):
    limiter.enabled = False
    with app.test_client() as c:
        yield c


@pytest.fixture
def limited_client(db):
    limiter.enabled = True
    with app.test_client() as c:
        yield c


def _login(client, pw, ip='8.8.4.4'):
    return client.post('/api/v1/auth/login', data=json.dumps({'password': pw}),
                       content_type='application/json',
                       environ_base={'REMOTE_ADDR': ip})


def test_lockout_retry_after_is_whole_seconds(client):
    for _ in range(LOCKOUT_THRESHOLD):
        _login(client, 'wrong')
    r = _login(client, PW)
    assert_response_conforms('post', '/auth/login', r, status=429)
    ra = r.headers['Retry-After']
    assert ra.isdigit() and int(ra) >= 1
    body = r.get_json()
    assert body['retryAfterSeconds'] == int(ra)
    assert body['status'] == 429 and body['error']


def test_rate_limit_429_has_retry_after_header_and_body(limited_client):
    last = None
    for _ in range(4):
        last = _login(limited_client, 'x', ip=PRIVATE_IP)
    assert_response_conforms('post', '/auth/login', last, status=429)
    ra = last.headers['Retry-After']
    assert ra.isdigit() and 1 <= int(ra) <= 61
    body = last.get_json()
    assert body['retryAfterSeconds'] == int(ra)
    assert body['status'] == 429 and body['error']


def test_retry_after_uses_the_slowest_breached_limit():
    from types import SimpleNamespace as L
    from api import limiter_retry_after_seconds
    limits = [L(breached=False, reset_at=10_100), L(breached=True, reset_at=10_030),
              L(breached=True, reset_at=13_500)]
    assert limiter_retry_after_seconds(limits, 10_000.4) == 3500
    assert limiter_retry_after_seconds([], 10_000) == 1
    assert limiter_retry_after_seconds([L(breached=True, reset_at=9_999)], 10_000) == 1


def test_other_rate_limited_routes_also_say_when_to_retry(limited_client, db):
    db.set_setting('app_password', '')
    last = None
    for _ in range(6):
        last = limited_client.get('/api/v1/system/config-backup')
    assert_response_conforms('get', '/system/config-backup', last, status=429)
    ra = last.headers['Retry-After']
    assert ra.isdigit() and 1 <= int(ra) <= 3601
    body = last.get_json()
    assert set(body) == {'error', 'status', 'retryAfterSeconds'}
    assert body['retryAfterSeconds'] == int(ra)
    assert body['status'] == 429
    assert body['error'] == '5 per 1 hour'


def test_default_limit_429_says_when_to_retry(limited_client, db):
    db.set_setting('app_password', '')
    last = None
    for _ in range(201):
        last = limited_client.get('/api/v1/auth/status')
        if last.status_code == 429:
            break
    assert last.status_code == 429
    assert int(last.headers['Retry-After']) == last.get_json()['retryAfterSeconds'] >= 1


def _until_limited(client, path):
    resp = None
    for _ in range(205):
        resp = client.get(path)
        if resp.status_code == 429:
            break
    return resp


def test_unmatched_api_path_is_never_rate_limited(limited_client, db):
    # flask-limiter exempts requests with no endpoint, so this stays a JSON 404.
    resp = _until_limited(limited_client, '/api/v1/nope')
    assert resp.status_code == 404 and resp.get_json()['status'] == 404


def test_app_level_429_under_api_is_json_with_retry_after():
    from types import SimpleNamespace
    from flask_limiter import RateLimitExceeded
    from api import _handle_app_rate_limited
    exc = RateLimitExceeded(SimpleNamespace(error_message=None, limit='200 per 1 minute'))
    with app.test_request_context('/api/v1/nope'):
        resp = _handle_app_rate_limited(exc)
    assert resp.status_code == 429 and resp.mimetype == 'application/json'
    body = resp.get_json()
    assert body == {'error': '200 per 1 minute', 'status': 429,
                    'retryAfterSeconds': int(resp.headers['Retry-After'])}


def test_app_route_429_keeps_its_body_and_adds_retry_after(limited_client, db):
    resp = _until_limited(limited_client, '/example-podcast')
    assert resp.status_code == 429 and resp.mimetype == 'text/html'
    assert int(resp.headers['Retry-After']) >= 1
    assert b'Too Many Requests' in resp.data


def test_login_without_a_limit_hit_has_no_retry_after(client):
    r = _login(client, 'wrong', ip=PRIVATE_IP)
    assert r.status_code == 401
    assert 'Retry-After' not in r.headers
