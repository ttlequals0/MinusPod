"""Double-submit CSRF protection for the Flask API.

The session cookie is ``SameSite=Strict``; this is the second layer. Mutating requests
must send an ``X-CSRF-Token`` header matching the non-HttpOnly ``minuspod_csrf`` cookie
set on authenticated responses, which a cross-origin attacker can neither read nor set.
"""
from __future__ import annotations

import secrets

from flask import Request, Response, request, session

import config
from api.auth_state import AUTH_HOOK_EXEMPT_ENDPOINTS, cookie_auth, current_auth


CSRF_COOKIE_NAME = 'minuspod_csrf'
CSRF_HEADER_NAME = 'X-CSRF-Token'
CSRF_SESSION_KEY = '_csrf_token'
SAFE_METHODS = frozenset({'GET', 'HEAD', 'OPTIONS'})


def get_or_create_token() -> str:
    token = session.get(CSRF_SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        session[CSRF_SESSION_KEY] = token
    return token


def apply_csrf_cookie(response: Response, cookie_secure: bool) -> Response:
    """Set the minuspod_csrf cookie on an authenticated response.

    Minting a token writes the session, so anonymous responses and endpoints that set the
    skip flag are left alone: a new session cookie could replace one the browser withheld
    on a cross-site navigation, and no CDN caches past it.
    """
    if response.headers.get('X-Skip-CSRF-Cookie'):
        response.headers.pop('X-Skip-CSRF-Cookie', None)
        return response
    if request.endpoint in AUTH_HOOK_EXEMPT_ENDPOINTS:
        return response
    state = cookie_auth()
    if state is None or not state.signed_in:
        return response
    token = get_or_create_token()
    existing = response.headers.get('Set-Cookie', '')
    if CSRF_COOKIE_NAME not in existing:
        response.set_cookie(
            CSRF_COOKIE_NAME,
            token,
            secure=cookie_secure,
            httponly=False,
            samesite='Strict',
            path=config.cookie_path(),
        )
    return response


def validate(request: Request) -> str | None:
    """Return None if the CSRF check passes, else a user-safe error string (callers send 403).

    Safe methods bypass. Mutating methods fail closed: the header must match the session
    token, and a session with no token is itself a failure (e.g. a stale pre-upgrade
    cookie). Unauthenticated sessions bypass because the auth layer 401s them first.
    """
    if request.method in SAFE_METHODS:
        return None
    if not current_auth().signed_in:
        return None
    expected = session.get(CSRF_SESSION_KEY)
    if not expected:
        return 'CSRF token missing or invalid'
    supplied = request.headers.get(CSRF_HEADER_NAME)
    if not supplied or not secrets.compare_digest(supplied, expected):
        return 'CSRF token missing or invalid'
    return None
