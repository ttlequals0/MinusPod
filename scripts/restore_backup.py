#!/usr/bin/env python3
"""Verify a MinusPod backup and prepare a cold SQLite restore."""

from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from maintenance_lock import acquire_offline_lock, release_lock
from secrets_crypto import decrypt_backup_file


def _integrity_check(path: Path) -> None:
    conn = sqlite3.connect(f'{path.resolve().as_uri()}?mode=ro&immutable=1', uri=True)
    try:
        result = conn.execute('PRAGMA integrity_check').fetchone()
    finally:
        conn.close()
    if not result or result[0] != 'ok':
        raise ValueError('SQLite integrity check failed')


def restore(source: Path, destination: Path, passphrase: str | None) -> None:
    source = source.resolve()
    destination = destination.resolve()
    if source == destination:
        raise ValueError('input and output must be different files')
    if destination.exists():
        raise FileExistsError(f'destination exists: {destination}')
    if any(Path(f'{destination}{suffix}').exists() for suffix in ('-wal', '-shm')):
        raise FileExistsError('destination has SQLite WAL or shared-memory sidecars')
    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp = destination.with_name(f'.{destination.name}.{uuid.uuid4().hex}.restore')
    try:
        with source.open('rb') as stream:
            magic = stream.read(7)
        if magic == b'MPBK02\x00':
            if not passphrase:
                raise ValueError('MINUSPOD_MASTER_PASSPHRASE is required')
            decrypt_backup_file(source, tmp, passphrase)
        elif magic == b'MPBK01\x00':
            raise ValueError(
                'legacy MPBK01 backups must first be decrypted with '
                'decrypt_backup.py and their source salt database'
            )
        else:
            fd = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with source.open('rb') as src, os.fdopen(fd, 'wb') as dst:
                shutil.copyfileobj(src, dst, 1024 * 1024)
                dst.flush()
                os.fsync(dst.fileno())
        _integrity_check(tmp)
        os.link(tmp, destination)
        os.unlink(tmp)
        directory_fd = os.open(destination.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        tmp.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description='Verify a backup for restore while the MinusPod container is stopped'
    )
    parser.add_argument('input', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    lock_fd = None
    try:
        lock_fd = acquire_offline_lock(args.output.parent)
        restore(
            args.input, args.output,
            os.environ.get('MINUSPOD_MASTER_PASSPHRASE'),
        )
    except Exception:
        print('restore failed; the destination was not published', file=sys.stderr)
        return 1
    finally:
        if lock_fd is not None:
            release_lock(lock_fd)
    print(f'verified backup written to {args.output}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
