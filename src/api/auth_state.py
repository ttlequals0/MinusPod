"""Shared authentication state and session revocation checks."""

import math
import os
import time

from flask import g, has_app_context, session

from config import (
    SESSION_LIFETIME_HOURS_MIN, SESSION_LIFETIME_HOURS_MAX,
    SESSION_REMEMBER_DAYS_MIN, SESSION_REMEMBER_DAYS_MAX,
    get_env_backed_int,
)


AUTH_GENERATION_KEY = 'auth_session_generation'
SESSION_GENERATION_KEY = 'auth_generation'
SESSION_REMEMBERED_KEY = 'auth_remembered'
SESSION_LAST_USED_KEY = 'auth_last_used'
LAST_USED_REFRESH_SECONDS = 60
CLOCK_SKEW_SECONDS = 60


def _now() -> float:
    return time.time()


def valid_last_used(value) -> bool:
    return (not isinstance(value, bool) and isinstance(value, (int, float))
            and math.isfinite(value))


_MEMO_ATTR = '_auth_state_memo'


def _memo(key, compute, fresh=False):
    """Value of compute() read once per request; fresh=True rereads (long-lived streams)."""
    if fresh or not has_app_context():
        return compute()
    store = g.__dict__.setdefault(_MEMO_ATTR, {})
    if key not in store:
        store[key] = compute()
    return store[key]


def forget_memo() -> None:
    if has_app_context():
        g.__dict__.pop(_MEMO_ATTR, None)


def session_limit_seconds(remembered: bool) -> int:
    """Idle limit for a session: the Remember me days or the normal hours."""
    def compute():
        if remembered:
            return get_env_backed_int(
                'session_remember_days', floor=SESSION_REMEMBER_DAYS_MIN,
                ceiling=SESSION_REMEMBER_DAYS_MAX) * 86400
        return get_env_backed_int(
            'session_lifetime_hours', floor=SESSION_LIFETIME_HOURS_MIN,
            ceiling=SESSION_LIFETIME_HOURS_MAX) * 3600
    return _memo(('limit', remembered), compute)


def current_generation(db, fresh=False) -> int:
    return _memo('generation', lambda: db.get_setting_int(AUTH_GENERATION_KEY, default=0), fresh)


def password_is_set(db) -> bool:
    return bool(db.get_setting('app_password'))


def authentication_required(db, fresh=False) -> bool:
    return _memo('required', lambda: (
        password_is_set(db)
        or os.environ.get('MINUSPOD_REQUIRE_AUTH', 'false').lower() == 'true'
    ), fresh)


def enforce_idle_limit(db) -> None:
    """Clear a signed-in session idle past its limit, else note this use (concurrent refreshes may be lost)."""
    if not session.get('authenticated') or not authentication_required(db):
        return
    now = _now()
    last_used = session.get(SESSION_LAST_USED_KEY)
    if not valid_last_used(last_used) or last_used - now > CLOCK_SKEW_SECONDS:
        session.clear()
        return
    limit = session_limit_seconds(bool(session.get(SESSION_REMEMBERED_KEY, False)))
    idle = now - last_used
    if idle > limit:
        session.clear()
    elif idle >= LAST_USED_REFRESH_SECONDS:
        session[SESSION_LAST_USED_KEY] = now


def session_is_authenticated(db) -> bool:
    if not authentication_required(db):
        return True
    enforce_idle_limit(db)
    if not session.get('authenticated', False):
        return False
    if session.get(SESSION_GENERATION_KEY) != current_generation(db):
        # A revoked session is dropped so its 401 deletes the cookie and mints no CSRF token.
        session.clear()
        return False
    return True


def authenticate_session(
        db, generation: int | None = None, remembered: bool = False) -> None:
    forget_memo()
    session.clear()
    session.permanent = True
    session['authenticated'] = True
    session[SESSION_GENERATION_KEY] = (
        current_generation(db) if generation is None else generation
    )
    session[SESSION_REMEMBERED_KEY] = remembered
    session[SESSION_LAST_USED_KEY] = _now()


def revoke_sessions(db) -> int:
    forget_memo()
    return db.increment_setting_int(AUTH_GENERATION_KEY)
