"""Shared authentication state and session revocation checks."""

import logging
import math
import os
import time
from dataclasses import dataclass, replace

from flask import g, has_request_context, request, session

from config import get_env_backed_int
from database import Database


logger = logging.getLogger('podcast.api')

AUTH_GENERATION_KEY = 'auth_session_generation'
SESSION_GENERATION_KEY = 'auth_generation'
SESSION_REMEMBERED_KEY = 'auth_remembered'
SESSION_LAST_USED_KEY = 'auth_last_used'
# Any of these in the session means a check is needed before cookies are written.
_AUTH_MARKER_KEYS = ('authenticated', SESSION_GENERATION_KEY, SESSION_LAST_USED_KEY)
LAST_USED_REFRESH_SECONDS = 60
CLOCK_SKEW_SECONDS = 60
# Probes answer without the session check so a database failure cannot turn them into a 500.
AUTH_HOOK_EXEMPT_ENDPOINTS = frozenset({'api.health_live', 'api.health_check'})
_HOLDER = 'auth_holder'


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
        # Leftover auth keys can never sign in; drop them so the cookie is not kept alive.
        if required and _session_has_auth_marker():
            session.clear()
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


class _RequestAuth:
    """Auth bookkeeping for one request; ignored by any other request sharing the same g."""
    __slots__ = ('request', 'pending', 'state', 'failed')

    def __init__(self, req, pending: bool):
        self.request = req
        self.pending = pending
        self.state = None
        self.failed = False


def _holder(create: bool = False) -> _RequestAuth | None:
    if not has_request_context():
        return None
    req = request._get_current_object()
    holder = g.get(_HOLDER)
    # A preserved test-client context can keep g alive across requests, so match the request.
    if holder is None or holder.request is not req:
        if not create:
            return None
        holder = _RequestAuth(req, pending=False)
        setattr(g, _HOLDER, holder)
    return holder


def refresh_auth(db=None, state: AuthState | None = None) -> AuthState | None:
    """Store this request's auth state: the one given, or a fresh session check; no-op outside a request."""
    if not has_request_context():
        return None
    if state is None:
        state = check_session(db or Database())
    holder = _holder(create=True)
    holder.state, holder.pending = state, False
    return state


def load_request_auth(check: bool = True) -> None:
    """Before-request hook: drop any earlier request's auth state and mark this one for a lazy check."""
    setattr(g, _HOLDER, _RequestAuth(
        request._get_current_object(),
        pending=check and request.endpoint not in AUTH_HOOK_EXEMPT_ENDPOINTS))


def stored_auth() -> AuthState | None:
    """The auth state already resolved for this request, or None."""
    holder = _holder()
    return holder.state if holder else None


def _record_failure(exc: Exception) -> None:
    holder = _holder(create=True)
    holder.state, holder.pending, holder.failed = None, False, True
    logger.error("Session check failed: %s: %s", type(exc).__name__, exc)


def resolve_auth() -> AuthState | None:
    """Run the pending session check once and cache it; None when it failed or the hook never ran."""
    holder = _holder()
    if holder is None:
        return None
    if holder.state is None and holder.pending:
        try:
            refresh_auth()
        except Exception as exc:
            _record_failure(exc)
    return holder.state


def _session_has_auth_marker() -> bool:
    return any(session.get(key) is not None for key in _AUTH_MARKER_KEYS)


def cookie_auth() -> AuthState | None:
    """State for cookie decisions: checked only when the session carries a sign-in marker."""
    holder = _holder()
    if holder is None:
        return None
    if holder.state is None and holder.pending and _session_has_auth_marker():
        return resolve_auth()
    return holder.state


def auth_check_failed() -> bool:
    """Whether this request's session check raised or found no stored state."""
    holder = _holder()
    return bool(holder and holder.failed and holder.state is None)


def note_missing_auth() -> None:
    """Log, once per request, that auth state was read before the hook stored it."""
    holder = _holder(create=True)
    if holder is not None and holder.failed:
        return
    logger.error("Auth state read before the request hook stored it; treating as signed out")
    if holder is not None:
        holder.failed = True


def current_auth() -> AuthState:
    """This request's auth state, checked on first use; signed out when the check is unavailable."""
    state = resolve_auth()
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
    # Stored first so a failing re-check cannot leave the earlier signed-in state in place.
    before = stored_auth() or _HOOK_MISSING
    refresh_auth(state=replace(before, authenticated=not before.required, signed_in=False,
                               remembered=False, limit_seconds=None))
    return refresh_auth(db)


def recheck_auth(db) -> AuthState:
    """Re-run the session check after a settings change; if it fails, nothing from before is trusted."""
    try:
        return refresh_auth(db)
    except Exception:
        holder = _holder(create=True)
        holder.state, holder.pending, holder.failed = None, False, True
        raise


def revoke_sessions(db) -> int:
    return db.increment_setting_int(AUTH_GENERATION_KEY)
