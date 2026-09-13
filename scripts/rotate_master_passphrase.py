#!/usr/bin/env python3
"""Rotate the master passphrase with all MinusPod workers stopped."""

from __future__ import annotations

import getpass
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from database import Database
from maintenance_lock import acquire_offline_lock, release_lock
from secrets_crypto import decrypt, is_ciphertext, reset_cache, rotate


def main() -> int:
    old = getpass.getpass('Current master passphrase: ')
    new = getpass.getpass('New master passphrase: ')
    confirm = getpass.getpass('Confirm new master passphrase: ')
    if not old or not new or new != confirm:
        print('passphrases are empty or do not match', file=sys.stderr)
        return 2
    lock_fd = None
    try:
        lock_fd = acquire_offline_lock()
        os.environ['MINUSPOD_MASTER_PASSPHRASE'] = old
        db = Database()
        count = rotate(db, old, new)
        os.environ['MINUSPOD_MASTER_PASSPHRASE'] = new
        reset_cache()
        for info in db.get_all_settings().values():
            value = info.get('value') if isinstance(info, dict) else None
            if is_ciphertext(value):
                decrypt(db, value)
    except Exception:
        print('rotation failed; the database was left unchanged or must be restored from the pre-rotation backup', file=sys.stderr)
        return 1
    finally:
        if lock_fd is not None:
            release_lock(lock_fd)
    print(f'rotated {count} encrypted settings')
    print('Update MINUSPOD_MASTER_PASSPHRASE before starting MinusPod')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
