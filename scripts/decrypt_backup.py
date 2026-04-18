#!/usr/bin/env python3
"""Decrypt a MinusPod encrypted backup file (``*.db.enc``).

Usage::

    MINUSPOD_MASTER_PASSPHRASE=... \\
        python scripts/decrypt_backup.py backup.db.enc backup.db

The passphrase must match what the container that produced the backup
had at the time of the export. The salt lives inside the container's
SQLite ``app_crypto_salt`` row, so decryption also needs access to that
DB -- the script reads ``DATA_PATH`` (default ``/app/data``) for it.
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
    from database import Database
    from secrets_crypto import decrypt_bytes

    db = Database(os.environ.get("DATA_PATH", "/app/data"))
    blob = src.read_bytes()
    plaintext = decrypt_bytes(db, blob)
    dst.write_bytes(plaintext)
    print(f"decrypted {len(blob)} -> {len(plaintext)} bytes: {dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
