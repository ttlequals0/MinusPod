#!/usr/bin/env python3
"""Standalone MinusPod backup decrypter.

Decrypts a MinusPod backup envelope (``*.db.enc``) without needing the
MinusPod source code installed. Reads the per-instance salt from any
SQLite file from the same instance (the running DB, or any already-
decrypted backup from that instance).

Requirements:
    pip install cryptography

Usage:
    MINUSPOD_MASTER_PASSPHRASE=your-passphrase \\
        python decrypt_backup_standalone.py \\
            --salt-db path/to/source.db \\
            backup.db.enc backup.db
"""
from __future__ import annotations

import argparse
import base64
import os
import sqlite3
import sys
from pathlib import Path

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    from cryptography.hazmat.primitives import hashes
except ImportError:
    print("error: install 'cryptography' first: pip install cryptography", file=sys.stderr)
    sys.exit(4)


# These constants mirror src/secrets_crypto.py. Do not change them.
BACKUP_MAGIC = b"MPBK01\x00"
NONCE_LEN = 12
PBKDF2_ITERATIONS = 600_000
DEK_LEN = 32
SALT_KEY = "provider_crypto_salt"


def read_salt(db_path: Path) -> bytes:
    """Pull the per-instance PBKDF2 salt out of a SQLite file.

    Works on both the live DB and any already-decrypted backup from the
    same instance. Returns raw salt bytes.
    """
    if not db_path.exists():
        raise FileNotFoundError(f"salt DB not found: {db_path}")
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT value FROM settings WHERE key = ?", (SALT_KEY,)
        ).fetchone()
    finally:
        conn.close()
    if not row or not row[0]:
        raise ValueError(
            f"{db_path} has no {SALT_KEY!r} row; not a MinusPod DB or pre-crypto version"
        )
    return base64.b64decode(row[0])


def derive_dek(passphrase: str, salt: bytes) -> bytes:
    """PBKDF2-HMAC-SHA256(600k) -> 32-byte AES-GCM key."""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=DEK_LEN,
        salt=salt,
        iterations=PBKDF2_ITERATIONS,
    )
    return kdf.derive(passphrase.encode("utf-8"))


def decrypt_envelope(envelope: bytes, dek: bytes) -> bytes:
    """Reverse of the MPBK01 envelope: magic + nonce + ciphertext+tag."""
    if not envelope.startswith(BACKUP_MAGIC):
        raise ValueError("not a MinusPod encrypted-backup envelope (magic mismatch)")
    body = envelope[len(BACKUP_MAGIC):]
    if len(body) < NONCE_LEN + 16:
        raise ValueError("envelope too short; file may be truncated")
    nonce = body[:NONCE_LEN]
    ct = body[NONCE_LEN:]
    return AESGCM(dek).decrypt(nonce, ct, None)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Decrypt a MinusPod encrypted backup without the MinusPod source tree"
    )
    parser.add_argument("input", type=Path, help="encrypted backup file (*.db.enc)")
    parser.add_argument("output", type=Path, help="where to write the decrypted SQLite file")
    parser.add_argument(
        "--salt-db",
        type=Path,
        required=True,
        help="path to any SQLite DB from the same instance (the running DB, "
             "or a previously-decrypted backup)",
    )
    args = parser.parse_args()

    passphrase = os.environ.get("MINUSPOD_MASTER_PASSPHRASE")
    if not passphrase:
        print("error: MINUSPOD_MASTER_PASSPHRASE environment variable is required", file=sys.stderr)
        return 3

    try:
        salt = read_salt(args.salt_db)
    except (FileNotFoundError, ValueError) as e:
        print(f"error reading salt: {e}", file=sys.stderr)
        return 5

    dek = derive_dek(passphrase, salt)

    blob = args.input.read_bytes()
    try:
        plaintext = decrypt_envelope(blob, dek)
    except Exception as e:
        print(f"decryption failed: {e}", file=sys.stderr)
        print(
            "likely causes: wrong passphrase, wrong salt DB, or corrupted file",
            file=sys.stderr,
        )
        return 6

    args.output.write_bytes(plaintext)
    print(f"decrypted {len(blob)} -> {len(plaintext)} bytes: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
