"""Tests for AuthLockoutMixin per-IP lockout tracking."""
import datetime
from unittest.mock import patch

import pytest

from database.auth_lockout import (
    LOCKOUT_DURATION_MINUTES,
    LOCKOUT_THRESHOLD,
)


def test_no_lockout_without_failures(temp_db):
    assert temp_db.check_lockout("203.0.113.5") is None


def test_lockout_triggers_after_threshold(temp_db):
    ip = "203.0.113.10"
    for i in range(LOCKOUT_THRESHOLD - 1):
        result = temp_db.record_auth_failure(ip)
        assert result is None, f"locked too early at attempt {i+1}"

    locked_until = temp_db.record_auth_failure(ip)
    assert locked_until is not None
    assert temp_db.check_lockout(ip) == locked_until


def test_success_clears_failure_state(temp_db):
    ip = "203.0.113.15"
    temp_db.record_auth_failure(ip)
    temp_db.record_auth_failure(ip)
    temp_db.record_auth_success(ip)

    assert temp_db.check_lockout(ip) is None
    result = temp_db.record_auth_failure(ip)
    assert result is None


def test_expired_lockout_is_not_reported(temp_db):
    ip = "203.0.113.20"
    for _ in range(LOCKOUT_THRESHOLD):
        temp_db.record_auth_failure(ip)

    with patch('database.auth_lockout._now_iso') as mock_now:
        future = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(
            minutes=LOCKOUT_DURATION_MINUTES + 1
        )
        mock_now.return_value = future.strftime("%Y-%m-%dT%H:%M:%SZ")
        assert temp_db.check_lockout(ip) is None


def test_empty_ip_does_nothing(temp_db):
    assert temp_db.record_auth_failure("") is None
    assert temp_db.check_lockout("") is None
    temp_db.record_auth_success("")


def test_cleanup_removes_expired_rows(temp_db):
    ip = "203.0.113.25"
    temp_db.record_auth_failure(ip)

    conn = temp_db.get_connection()
    old = (
        datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=2)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn.execute(
        "UPDATE auth_failures SET first_failed_at = ?, last_failed_at = ? WHERE ip = ?",
        (old, old, ip),
    )
    conn.commit()

    deleted = temp_db.cleanup_auth_failures()
    assert deleted == 1
    assert temp_db.check_lockout(ip) is None
