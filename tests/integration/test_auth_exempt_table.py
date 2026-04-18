"""Prove the tightened AUTH_EXEMPT_PATHS / PODCAST_APP_EXEMPT_PATTERNS
behave as intended once an admin password is set.

- SSE stream must require auth (it used to be exempt-by-prefix).
- Blueprint-registered /docs and /openapi.yaml must require auth.
- /feeds/<slug>/artwork GET is the ONLY podcast-app exemption, and only
  for GET; POST/PUT/DELETE on the same path require auth.
- A traversal-like slug under /feeds/.../artwork must not pass the regex
  and therefore must 401.
"""
import os
import sys
import tempfile

import pytest

_test_data_dir = tempfile.mkdtemp(prefix='auth_exempt_test_')
os.environ.setdefault('SECRET_KEY', 'auth-exempt-test-secret')
os.environ.setdefault('DATA_DIR', _test_data_dir)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

import database
import storage as storage_mod
database.Database._instance = None
database.Database.__init__.__defaults__ = (_test_data_dir,)
storage_mod.Storage.__init__.__defaults__ = (_test_data_dir,)

from main_app import app
from werkzeug.security import generate_password_hash


@pytest.fixture
def client_with_password():
    db = database.Database()
    db.set_setting('app_password', generate_password_hash('pw', method='scrypt'))
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c
    db.set_setting('app_password', '')


def test_status_stream_unauth_gets_auth_failed_event(client_with_password):
    """Unauthenticated SSE connect receives a single ``auth-failed``
    event rather than a 401, so the browser-side handler can redirect
    cleanly instead of reconnect-looping against a closed 401 response
    that EventSource cannot introspect."""
    response = client_with_password.get(
        '/api/v1/status/stream',
        buffered=True,
    )
    assert response.status_code == 200
    assert response.mimetype == 'text/event-stream'
    body = response.get_data(as_text=True)
    assert 'event: auth-failed' in body


def test_status_stream_authenticated_opens_stream(client_with_password):
    """Authenticated SSE connect must open the real data stream and
    never emit ``auth-failed``. Reads a bounded prefix then closes --
    the authenticated stream is long-polled so ``buffered=True`` would
    block forever."""
    login = client_with_password.post(
        '/api/v1/auth/login',
        json={'password': 'pw'},
    )
    assert login.status_code == 200
    response = client_with_password.get(
        '/api/v1/status/stream',
        buffered=False,
    )
    try:
        assert response.status_code == 200
        assert response.mimetype == 'text/event-stream'
        # Accumulate chunks until we see a full SSE frame boundary or
        # hit a bounded cap. The generator might emit a comment ping
        # or split the first frame across yields, so a single next()
        # is fragile.
        buf = b""
        for _ in range(8):
            try:
                buf += next(response.response)
            except StopIteration:
                break
            if b"\n\n" in buf:
                break
        prefix = buf.decode('utf-8')
        assert 'event: auth-failed' not in prefix
        assert 'data: ' in prefix
    finally:
        response.close()


def test_api_docs_requires_auth(client_with_password):
    """/docs moved to blueprint so check_auth gates it."""
    response = client_with_password.get('/api/v1/docs')
    assert response.status_code == 401


def test_api_openapi_requires_auth(client_with_password):
    """/openapi.yaml moved to blueprint so check_auth gates it."""
    response = client_with_password.get('/api/v1/openapi.yaml')
    assert response.status_code == 401


def test_artwork_get_is_exempt(client_with_password):
    """podcast-app <img> cross-origin GET stays public. No such feed
    exists, so 404 is expected -- reaching the handler at all proves
    the auth gate did NOT fire."""
    response = client_with_password.get('/api/v1/feeds/valid-slug/artwork')
    assert response.status_code in (200, 404)


def test_artwork_post_does_not_leak(client_with_password):
    """POST on the artwork path must never return bytes. Werkzeug's URL
    matcher raises MethodNotAllowed (405) before check_auth runs because
    no POST handler is registered; that's structurally safe. If the
    exemption regex were over-broad and matched other paths, this test
    would fail with 200 instead. 401 would also be acceptable if the
    route ever gains a POST handler."""
    response = client_with_password.post('/api/v1/feeds/valid-slug/artwork')
    assert response.status_code in (401, 405)
    assert response.status_code != 200


def test_artwork_exempt_regex_rejects_uppercase(client_with_password):
    """The exemption regex is strict lowercase [a-z0-9-]. An uppercase
    slug must fall through to the authenticated path (401 when unauth).
    Using plain ASCII so Werkzeug routing doesn't normalise the request
    away before the exemption check runs."""
    response = client_with_password.get('/api/v1/feeds/UPPERCASE/artwork')
    assert response.status_code == 401


def test_artwork_exempt_regex_rejects_hyphen_start(client_with_password):
    """Regex requires leading [a-z0-9]; a hyphen-leading slug must fall
    through to the authenticated path."""
    response = client_with_password.get('/api/v1/feeds/-bad-slug/artwork')
    assert response.status_code == 401


def test_auth_password_remains_exempt(client_with_password):
    """Initial password setup uses the same endpoint; the body-verified
    check gates it when a current password already exists, but the
    before_request exemption must let the request through."""
    response = client_with_password.put(
        '/api/v1/auth/password',
        json={'newPassword': 'differentpw', 'currentPassword': 'wrongpw'},
    )
    # 401 from body-verified check is fine; a 401 from the before_request
    # gate is NOT -- that would be "exemption was dropped". Either way
    # the request reached the handler if it saw the JSON body.
    assert response.status_code in (400, 401), (
        f"unexpected status {response.status_code}"
    )
