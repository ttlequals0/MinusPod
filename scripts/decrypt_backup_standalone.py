#!/usr/bin/env python3
"""Standalone MinusPod backup decrypter.

Decrypts a MinusPod backup envelope (``*.db.enc``) without the MinusPod
source tree. Current MPBK02 backups are self-contained. Legacy MPBK01
backups also need a SQLite database from the same instance for its salt.

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
import struct
import sys
import uuid
from pathlib import Path

try:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    from cryptography.hazmat.primitives import hashes
except ImportError:
    print("error: install 'cryptography' first: pip install cryptography", file=sys.stderr)
    sys.exit(4)


# These constants mirror src/secrets_crypto.py. Do not change them.
BACKUP_MAGIC = b"MPBK01\x00"
BACKUP_MAGIC_V2 = b"MPBK02\x00"
BACKUP_HEADER = struct.Struct(">7sI16s12sQ")
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


def decrypt_v2(source: Path, output: Path, passphrase: str) -> None:
    if output.exists():
        raise FileExistsError(f'output already exists: {output}')
    tmp = output.with_name(f'.{output.name}.{uuid.uuid4().hex}.tmp')
    try:
        with source.open('rb') as src:
            header = src.read(BACKUP_HEADER.size)
            magic, iterations, salt, nonce, size = BACKUP_HEADER.unpack(header)
            if magic != BACKUP_MAGIC_V2 or iterations != PBKDF2_ITERATIONS:
                raise ValueError('unsupported backup header')
            ciphertext_size = source.stat().st_size - len(header) - 16
            if ciphertext_size != size:
                raise ValueError('backup length does not match its header')
            src.seek(-16, os.SEEK_END)
            tag = src.read(16)
            src.seek(len(header))
            decryptor = Cipher(
                algorithms.AES(derive_dek(passphrase, salt)), modes.GCM(nonce, tag)
            ).decryptor()
            decryptor.authenticate_additional_data(header)
            fd = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, 'wb') as dst:
                remaining = ciphertext_size
                while remaining:
                    chunk = src.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise ValueError('backup ciphertext is truncated')
                    remaining -= len(chunk)
                    dst.write(decryptor.update(chunk))
                dst.write(decryptor.finalize())
                dst.flush()
                os.fsync(dst.fileno())
        os.link(tmp, output)
        tmp.unlink()
    finally:
        tmp.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Decrypt a MinusPod encrypted backup without the MinusPod source tree"
    )
    parser.add_argument("input", type=Path, help="encrypted backup file (*.db.enc)")
    parser.add_argument("output", type=Path, help="where to write the decrypted SQLite file")
    parser.add_argument(
        "--salt-db",
        type=Path,
        required=False,
        help="path to any SQLite DB from the same instance (the running DB, "
             "or a previously-decrypted backup)",
    )
    args = parser.parse_args()

    passphrase = os.environ.get("MINUSPOD_MASTER_PASSPHRASE")
    if not passphrase:
        print("error: MINUSPOD_MASTER_PASSPHRASE environment variable is required", file=sys.stderr)
        return 3

    try:
        with args.input.open('rb') as source:
            magic = source.read(7)
        if magic == BACKUP_MAGIC_V2:
            decrypt_v2(args.input, args.output, passphrase)
        else:
            if args.salt_db is None:
                raise ValueError('--salt-db is required for legacy MPBK01 backups')
            salt = read_salt(args.salt_db)
            plaintext = decrypt_envelope(args.input.read_bytes(), derive_dek(passphrase, salt))
            fd = os.open(args.output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, 'wb') as output:
                output.write(plaintext)
    except Exception as e:
        print(f"decryption failed: {e}", file=sys.stderr)
        print(
            "likely causes: wrong passphrase, wrong salt DB, or corrupted file",
            file=sys.stderr,
        )
        return 6

    print(f"decrypted {args.input.stat().st_size} bytes: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
