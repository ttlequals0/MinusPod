from datetime import datetime, timedelta, timezone

from flask.sessions import SecureCookieSessionInterface

from api.auth_state import SESSION_REMEMBERED_KEY, session_limit_seconds


class IdleExpiryCookieSessionInterface(SecureCookieSessionInterface):
    """Expire the session cookie after this session's own idle limit."""

    def get_expiration_time(self, app, session):
        if not session.permanent:
            return None
        seconds = session_limit_seconds(bool(session.get(SESSION_REMEMBERED_KEY, False)))
        return datetime.now(timezone.utc) + timedelta(seconds=seconds)
