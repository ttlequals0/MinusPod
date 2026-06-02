#!/usr/bin/env python3
"""Decrypt a MinusPod encrypted backup file (``*.db.enc``).

Usage::

    MINUSPOD_MASTER_PASSPHRASE=... \\
        python scripts/decrypt_backup.py backup.db.enc backup.db

The passphrase must match what the container that produced the backup
had at the time of the export. The salt lives inside the container's
SQLite ``provider_crypto_salt`` row, so decryption also needs access to
that DB -- the script reads ``DATA_PATH`` (default ``/app/data``) for it,
read-only, without constructing the full Database (which would create
tables, run migrations, and could mint a new salt).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2

    src = Path(sys.argv[1])
    dst = Path(sys.argv[2])

    if not os.environ.get("MINUSPOD_MASTER_PASSPHRASE"):
        print("error: MINUSPOD_MASTER_PASSPHRASE is required", file=sys.stderr)
        return 3

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from secrets_crypto import decrypt_bytes

    db = _ReadOnlySaltDB(os.environ.get("DATA_PATH", "/app/data"))
    blob = src.read_bytes()
    plaintext = decrypt_bytes(db, blob)
    dst.write_bytes(plaintext)
    print(f"decrypted {len(blob)} -> {len(plaintext)} bytes: {dst}")
    return 0


class _ReadOnlySaltDB:
    """Minimal read-only DB shim exposing ``get_setting`` against the live
    SQLite file. Lets the decrypter derive the DEK (which only needs the salt
    row) without constructing the full Database singleton, whose __init__ runs
    mkdir + CREATE TABLE migrations and could mint a fresh salt that orphans
    the very data being recovered (backup-scripts-1)."""

    def __init__(self, data_dir: str):
        self._path = Path(data_dir) / "podcast.db"

    def get_setting(self, key: str):
        import sqlite3
        if not self._path.exists():
            return None
        conn = sqlite3.connect(f"file:{self._path}?mode=ro", uri=True)
        try:
            row = conn.execute(
                "SELECT value FROM settings WHERE key = ?", (key,)
            ).fetchone()
            return row[0] if row else None
        finally:
            conn.close()

    def set_setting(self, *args, **kwargs):
        raise RuntimeError("decrypt_backup is read-only and must not write to the DB")


if __name__ == "__main__":
    raise SystemExit(main())
