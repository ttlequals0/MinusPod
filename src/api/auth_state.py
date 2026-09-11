"""Shared authentication state and session revocation checks."""

import os

from flask import session


AUTH_GENERATION_KEY = 'auth_session_generation'
SESSION_GENERATION_KEY = 'auth_generation'


def current_generation(db) -> int:
    return db.get_setting_int(AUTH_GENERATION_KEY, default=0)


def password_is_set(db) -> bool:
    return bool(db.get_setting('app_password'))


def authentication_required(db) -> bool:
    return (
        password_is_set(db)
        or os.environ.get('MINUSPOD_REQUIRE_AUTH', 'false').lower() == 'true'
    )


def session_is_authenticated(db) -> bool:
    if not authentication_required(db):
        return True
    return bool(
        session.get('authenticated', False)
        and session.get(SESSION_GENERATION_KEY) == current_generation(db)
    )


def authenticate_session(db, generation: int | None = None) -> None:
    session.clear()
    session.permanent = True
    session['authenticated'] = True
    session[SESSION_GENERATION_KEY] = (
        current_generation(db) if generation is None else generation
    )


def revoke_sessions(db) -> int:
    return db.increment_setting_int(AUTH_GENERATION_KEY)
