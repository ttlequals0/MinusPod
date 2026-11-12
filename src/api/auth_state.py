"""Shared authentication state and session revocation checks."""

import logging
import math
import os
import time
from dataclasses import dataclass

from flask import g, has_request_context, jsonify, request, session

from config import get_env_backed_int
from database import Database


logger = logging.getLogger('podcast.api')

AUTH_GENERATION_KEY = 'auth_session_generation'
SESSION_GENERATION_KEY = 'auth_generation'
SESSION_REMEMBERED_KEY = 'auth_remembered'
SESSION_LAST_USED_KEY = 'auth_last_used'
LAST_USED_REFRESH_SECONDS = 60
CLOCK_SKEW_SECONDS = 60
# Probes answer without the session check so a database failure cannot turn them into a 500.
AUTH_HOOK_EXEMPT_ENDPOINTS = frozenset({'api.health_live', 'api.health_check'})
_FAILURE_LOGGED = 'auth_failure_logged'


@dataclass(frozen=True)
class AuthState:
    """One request's auth result; signed_in means the session holds a live sign-in."""
    password_set: bool
    required: bool
    authenticated: bool
    signed_in: bool
    remembered: bool = False
    limit_seconds: int | None = None


# Used when a request reads auth state the request hook never stored.
_HOOK_MISSING = AuthState(password_set=True, required=True, authenticated=False, signed_in=False)


def _now() -> float:
    return time.time()


def valid_last_used(value) -> bool:
    return (not isinstance(value, bool) and isinstance(value, (int, float))
            and math.isfinite(value))


def session_limit_seconds(remembered: bool) -> int:
    """Idle limit for a session: the Remember me days or the normal hours."""
    if remembered:
        return get_env_backed_int('session_remember_days') * 86400
    return get_env_backed_int('session_lifetime_hours') * 3600


def current_generation(db) -> int:
    return db.get_setting_int(AUTH_GENERATION_KEY, default=0)


def password_is_set(db) -> bool:
    return bool(db.get_setting('app_password'))


def _auth_forced() -> bool:
    return os.environ.get('MINUSPOD_REQUIRE_AUTH', 'false').lower() == 'true'


def authentication_required(db) -> bool:
    return password_is_set(db) or _auth_forced()


def check_session(db) -> AuthState:
    """Apply the idle and generation checks to the request session, clearing it when they fail."""
    password_set = password_is_set(db)
    required = password_set or _auth_forced()
    if not session.get('authenticated'):
        return AuthState(password_set, required, authenticated=not required, signed_in=False)
    remembered = bool(session.get(SESSION_REMEMBERED_KEY, False))
    if not required:
        return AuthState(password_set, required, authenticated=True, signed_in=True,
                         remembered=remembered)
    signed_out = AuthState(password_set, required, authenticated=False, signed_in=False)
    now = _now()
    last_used = session.get(SESSION_LAST_USED_KEY)
    if not valid_last_used(last_used) or last_used - now > CLOCK_SKEW_SECONDS:
        session.clear()
        return signed_out
    limit = session_limit_seconds(remembered)
    idle = now - last_used
    # A revoked session is dropped so its response deletes the cookie and mints no CSRF token.
    if idle > limit or session.get(SESSION_GENERATION_KEY) != current_generation(db):
        session.clear()
        return signed_out
    # Concurrent refreshes may be lost; that only shortens the idle period.
    if idle >= LAST_USED_REFRESH_SECONDS:
        session[SESSION_LAST_USED_KEY] = now
    return AuthState(password_set, required, authenticated=True, signed_in=True,
                     remembered=remembered, limit_seconds=limit)


def refresh_auth(db=None, state: AuthState | None = None) -> AuthState | None:
    """Store this request's auth state: the one given, or a fresh session check; no-op outside a request."""
    if not has_request_context():
        return None
    if state is None:
        state = check_session(db or Database())
    g.auth = state
    return state


def load_request_auth():
    """Before-request hook: run the session check once; a failure answers 500 before the view."""
    if request.endpoint in AUTH_HOOK_EXEMPT_ENDPOINTS:
        return None
    try:
        refresh_auth()
    except Exception as e:
        g.pop('auth', None)
        setattr(g, _FAILURE_LOGGED, True)
        logger.error("Session check failed: %s: %s", type(e).__name__, e)
        if request.path.startswith('/api/'):
            return jsonify({'error': 'Internal server error', 'status': 500}), 500
        return 'Internal Server Error', 500
    return None


def stored_auth() -> AuthState | None:
    """The auth state the request hook stored, or None."""
    return g.get('auth') if has_request_context() else None


def note_missing_auth() -> None:
    """Log, once per request, that auth state was read before the hook stored it."""
    if has_request_context() and g.get(_FAILURE_LOGGED):
        return
    logger.error("Auth state read before the request hook stored it; treating as signed out")
    if has_request_context():
        setattr(g, _FAILURE_LOGGED, True)


def current_auth() -> AuthState:
    """This request's auth state as the request hook stored it."""
    state = stored_auth()
    if state is None:
        note_missing_auth()
        return _HOOK_MISSING
    return state


def authenticate_session(
        db, generation: int | None = None, remembered: bool = False) -> None:
    session.clear()
    session.permanent = True
    session['authenticated'] = True
    session[SESSION_GENERATION_KEY] = (
        current_generation(db) if generation is None else generation
    )
    session[SESSION_REMEMBERED_KEY] = remembered
    session[SESSION_LAST_USED_KEY] = _now()
    refresh_auth(state=AuthState(
        password_set=True, required=True, authenticated=True, signed_in=True,
        remembered=remembered, limit_seconds=session_limit_seconds(remembered)))


def sign_out(db) -> AuthState:
    """Clear the session and record the signed-out state for this request."""
    session.clear()
    return refresh_auth(db)


def revoke_sessions(db) -> int:
    return db.increment_setting_int(AUTH_GENERATION_KEY)
