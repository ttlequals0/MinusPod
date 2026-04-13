"""Authenticated-encryption wrapper for provider API keys.

Secrets are stored in the ``settings`` table using the envelope
``enc:v1:<b64(nonce)>:<b64(ciphertext_with_tag)>``.  The DEK is derived
once per process via PBKDF2-HMAC-SHA256 from ``MINUSPOD_MASTER_PASSPHRASE``
and a random 16-byte salt persisted as setting ``provider_crypto_salt``.

The feature is locked when ``MINUSPOD_MASTER_PASSPHRASE`` is unset;
callers must check ``is_available()`` or handle ``CryptoUnavailableError``.
"""
import base64
import logging
import os
import secrets
import threading

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

logger = logging.getLogger(__name__)

ENVELOPE_PREFIX = "enc:v1:"
_SALT_KEY = "provider_crypto_salt"
_PBKDF2_ITERATIONS = 600_000
_KEY_LEN = 32
_SALT_LEN = 16
_NONCE_LEN = 12

_lock = threading.Lock()
_dek_cache: bytes | None = None


class CryptoUnavailableError(RuntimeError):
    """Raised when provider encryption is requested but not configured."""


def is_available() -> bool:
    return bool(os.environ.get("MINUSPOD_MASTER_PASSPHRASE"))


def is_ciphertext(value: str | None) -> bool:
    return bool(value) and value.startswith(ENVELOPE_PREFIX)


def _load_or_create_salt(db) -> bytes:
    existing = db.get_setting(_SALT_KEY)
    if existing:
        try:
            salt = base64.b64decode(existing)
            if len(salt) == _SALT_LEN:
                return salt
        except (ValueError, TypeError):
            logger.warning("provider_crypto_salt corrupt; regenerating")
    salt = secrets.token_bytes(_SALT_LEN)
    db.set_setting(_SALT_KEY, base64.b64encode(salt).decode("ascii"))
    return salt


def _derive_dek(db) -> bytes:
    global _dek_cache
    if _dek_cache is not None:
        return _dek_cache
    passphrase = os.environ.get("MINUSPOD_MASTER_PASSPHRASE")
    if not passphrase:
        raise CryptoUnavailableError("MINUSPOD_MASTER_PASSPHRASE is not set")
    with _lock:
        if _dek_cache is not None:
            return _dek_cache
        salt = _load_or_create_salt(db)
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=_KEY_LEN,
            salt=salt,
            iterations=_PBKDF2_ITERATIONS,
        )
        _dek_cache = kdf.derive(passphrase.encode("utf-8"))
    return _dek_cache


def reset_cache() -> None:
    """Test hook: clear the cached DEK."""
    global _dek_cache
    with _lock:
        _dek_cache = None


def rotate(db, old_passphrase: str, new_passphrase: str) -> int:
    """Re-encrypt every ``enc:v1:`` row under a new passphrase + new salt.

    Order matters: decrypt all rows up-front using the current DEK, mint a new
    DEK in memory, then write all new ciphertexts and the new salt inside a
    single SQLite transaction so a mid-rotation crash leaves the database
    consistent.

    The caller MUST update ``MINUSPOD_MASTER_PASSPHRASE`` in the container
    environment to ``new_passphrase`` before the next restart, otherwise the
    boot-time DEK derivation will not match the rotated ciphertext.

    Multi-worker note: the new DEK is cached in the calling worker's memory
    only.  Other Gunicorn workers retain the old cached DEK and will fail to
    decrypt the rotated rows until the container is restarted.  Restart
    immediately after rotating the env var.
    """
    current = os.environ.get("MINUSPOD_MASTER_PASSPHRASE")
    if not current:
        raise CryptoUnavailableError("MINUSPOD_MASTER_PASSPHRASE is not set")
    if old_passphrase != current:
        raise ValueError("current passphrase mismatch")
    if not new_passphrase:
        raise ValueError("new passphrase required")
    if new_passphrase == old_passphrase:
        raise ValueError("new passphrase must differ from current")

    # Decrypt under the current DEK while it is still authoritative.
    plaintexts: dict[str, str] = {}
    for key, info in db.get_all_settings().items():
        val = info.get("value") if isinstance(info, dict) else None
        if key == _SALT_KEY or not is_ciphertext(val):
            continue
        plaintexts[key] = decrypt(db, val)

    new_salt = secrets.token_bytes(_SALT_LEN)
    new_dek = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=_KEY_LEN,
        salt=new_salt,
        iterations=_PBKDF2_ITERATIONS,
    ).derive(new_passphrase.encode("utf-8"))

    aead = AESGCM(new_dek)
    fresh_envelopes = {}
    for key, plaintext in plaintexts.items():
        nonce = secrets.token_bytes(_NONCE_LEN)
        ct = aead.encrypt(nonce, plaintext.encode("utf-8"), None)
        fresh_envelopes[key] = (
            ENVELOPE_PREFIX
            + base64.b64encode(nonce).decode("ascii")
            + ":"
            + base64.b64encode(ct).decode("ascii")
        )

    conn = db.get_connection()
    salt_b64 = base64.b64encode(new_salt).decode("ascii")
    with conn:
        for key, envelope in fresh_envelopes.items():
            conn.execute(
                """INSERT INTO settings (key, value, is_default, updated_at)
                   VALUES (?, ?, 0, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
                   ON CONFLICT(key) DO UPDATE SET
                     value = excluded.value,
                     is_default = 0,
                     updated_at = excluded.updated_at""",
                (key, envelope),
            )
        conn.execute(
            """INSERT INTO settings (key, value, is_default, updated_at)
               VALUES (?, ?, 0, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
               ON CONFLICT(key) DO UPDATE SET
                 value = excluded.value,
                 updated_at = excluded.updated_at""",
            (_SALT_KEY, salt_b64),
        )

    global _dek_cache
    with _lock:
        _dek_cache = new_dek

    return len(fresh_envelopes)


def encrypt(db, plaintext: str) -> str:
    if plaintext is None:
        raise ValueError("plaintext required")
    dek = _derive_dek(db)
    nonce = secrets.token_bytes(_NONCE_LEN)
    ct = AESGCM(dek).encrypt(nonce, plaintext.encode("utf-8"), None)
    return (
        ENVELOPE_PREFIX
        + base64.b64encode(nonce).decode("ascii")
        + ":"
        + base64.b64encode(ct).decode("ascii")
    )


def decrypt(db, envelope: str) -> str:
    if not is_ciphertext(envelope):
        raise ValueError("not a v1 ciphertext envelope")
    body = envelope[len(ENVELOPE_PREFIX):]
    try:
        nonce_b64, ct_b64 = body.split(":", 1)
        nonce = base64.b64decode(nonce_b64)
        ct = base64.b64decode(ct_b64)
    except (ValueError, TypeError) as exc:
        raise ValueError("malformed ciphertext envelope") from exc
    dek = _derive_dek(db)
    return AESGCM(dek).decrypt(nonce, ct, None).decode("utf-8")
