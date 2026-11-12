from datetime import datetime, timedelta, timezone

from flask import g
from flask.sessions import SecureCookieSessionInterface
from itsdangerous import BadSignature

from api.auth_state import (
    SESSION_LAST_USED_KEY, SESSION_REMEMBERED_KEY, session_limit_seconds,
    valid_last_used,
)


class IdleExpiryCookieSessionInterface(SecureCookieSessionInterface):
    """Per-session cookie expiry, and a signing-time last-used for pre-upgrade cookies."""

    def should_set_cookie(self, app, session):
        # Without a session check this request (probes, feeds, a failed check) only a change is saved.
        if g.get('auth') is None:
            return session.modified
        return super().should_set_cookie(app, session)

    def get_expiration_time(self, app, session):
        if not session.permanent:
            return None
        auth = g.get('auth')
        if auth is not None and auth.limit_seconds is not None:
            seconds = auth.limit_seconds
        else:
            seconds = session_limit_seconds(bool(session.get(SESSION_REMEMBERED_KEY, False)))
        return datetime.now(timezone.utc) + timedelta(seconds=seconds)

    def open_session(self, app, request):
        serializer = self.get_signing_serializer(app)
        if serializer is None:
            return None
        cookie = request.cookies.get(self.get_cookie_name(app))
        if not cookie:
            return self.session_class()
        max_age = int(app.permanent_session_lifetime.total_seconds())
        try:
            data, signed_at = serializer.loads(cookie, max_age=max_age, return_timestamp=True)
        except BadSignature:
            return self.session_class()
        # No stamp means a cookie from before last-used was recorded: age it from signing.
        if data.get('authenticated') and not valid_last_used(data.get(SESSION_LAST_USED_KEY)):
            data[SESSION_LAST_USED_KEY] = signed_at.timestamp()
        return self.session_class(data)
