"""Shared helpers for API endpoints that write encrypted secrets.

Every secret-bearing route needs the same rules: reject writes when crypto is
unavailable (so plaintext never hits disk), encrypt a provided value or clear
otherwise, and map deep crypto failures to the same 409 as the shallow gate.
"""
from __future__ import annotations

from secrets_crypto import CryptoUnavailableError, is_available as crypto_available


class SecretWriteRejected(Exception):
    """Raised when a caller must return 409 provider_crypto_unavailable."""


def set_or_clear_secret(db, key: str, value: str | None) -> None:
    """Encrypt-and-store ``value`` when non-empty, else clear the row.

    Raises ``SecretWriteRejected`` when the value is non-empty but crypto is unusable;
    callers map that to a 409. Pre-check with ``crypto_available()`` to fail early.
    """
    stripped = (value or "").strip()
    if not stripped:
        db.clear_secret(key)
        return
    if not crypto_available():
        raise SecretWriteRejected(key)
    try:
        db.set_secret(key, stripped)
    except CryptoUnavailableError as exc:
        raise SecretWriteRejected(key) from exc
