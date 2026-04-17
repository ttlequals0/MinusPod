"""Auth-failure lockout mixin.

Tracks failed login attempts per source IP and blocks further attempts once a
threshold is crossed in the rolling window. Counters live in the ``auth_failures``
table so the decision is consistent across gunicorn workers (the flask-limiter
in-memory store is per-worker; lockout must be global or attackers can sidestep
it by getting routed to a second worker).
"""
from __future__ import annotations

import datetime
import logging
from typing import Optional

logger = logging.getLogger(__name__)

LOCKOUT_THRESHOLD = 5
LOCKOUT_WINDOW_MINUTES = 15
LOCKOUT_DURATION_MINUTES = 15


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _future_iso(minutes: int) -> str:
    future = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=minutes)
    return future.strftime("%Y-%m-%dT%H:%M:%SZ")


class AuthLockoutMixin:
    """Per-IP auth-failure tracking for the login endpoint."""

    def check_lockout(self, ip: str) -> Optional[str]:
        """Return the ISO 8601 ``locked_until`` timestamp if ``ip`` is currently
        locked out, else None. Callers translate to 429 / Retry-After.
        """
        if not ip:
            return None
        conn = self.get_connection()
        row = conn.execute(
            "SELECT locked_until FROM auth_failures WHERE ip = ?",
            (ip,),
        ).fetchone()
        if not row or not row["locked_until"]:
            return None
        if row["locked_until"] <= _now_iso():
            return None
        return row["locked_until"]

    def record_auth_failure(self, ip: str) -> Optional[str]:
        """Record a failed login for ``ip``. Returns the ``locked_until``
        timestamp when the failure crossed the lockout threshold; returns
        None otherwise.
        """
        if not ip:
            return None
        now = _now_iso()
        conn = self.get_connection()
        row = conn.execute(
            "SELECT failed_count, first_failed_at FROM auth_failures WHERE ip = ?",
            (ip,),
        ).fetchone()

        window_start = (
            datetime.datetime.now(datetime.timezone.utc)
            - datetime.timedelta(minutes=LOCKOUT_WINDOW_MINUTES)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")

        if not row or (row["first_failed_at"] and row["first_failed_at"] < window_start):
            count = 1
            first_failed = now
        else:
            count = int(row["failed_count"] or 0) + 1
            first_failed = row["first_failed_at"] or now

        locked_until = _future_iso(LOCKOUT_DURATION_MINUTES) if count >= LOCKOUT_THRESHOLD else None

        conn.execute(
            """INSERT INTO auth_failures (ip, failed_count, first_failed_at, last_failed_at, locked_until)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(ip) DO UPDATE SET
                 failed_count = excluded.failed_count,
                 first_failed_at = excluded.first_failed_at,
                 last_failed_at = excluded.last_failed_at,
                 locked_until = excluded.locked_until""",
            (ip, count, first_failed, now, locked_until),
        )
        conn.commit()

        if locked_until:
            logger.warning(
                "Auth lockout triggered for ip=%s (failed_count=%d locked_until=%s)",
                ip, count, locked_until,
            )
        return locked_until

    def record_auth_success(self, ip: str) -> None:
        """Clear any accumulated failure state for ``ip`` on successful login."""
        if not ip:
            return
        conn = self.get_connection()
        conn.execute("DELETE FROM auth_failures WHERE ip = ?", (ip,))
        conn.commit()

    def cleanup_auth_failures(self) -> int:
        """Remove rows whose window expired and whose lockout (if any) is
        also in the past. Callers invoke this from the existing cleanup task.
        Returns the count of deleted rows for telemetry.
        """
        now = _now_iso()
        window_start = (
            datetime.datetime.now(datetime.timezone.utc)
            - datetime.timedelta(minutes=LOCKOUT_WINDOW_MINUTES)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        conn = self.get_connection()
        cursor = conn.execute(
            """DELETE FROM auth_failures
               WHERE (locked_until IS NULL OR locked_until < ?)
                 AND last_failed_at < ?""",
            (now, window_start),
        )
        conn.commit()
        return cursor.rowcount or 0
