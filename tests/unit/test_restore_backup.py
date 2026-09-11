"""Cold restore verifies encryption and SQLite integrity before publication."""

import os
import sqlite3

import pytest

from scripts.restore_backup import restore
from secrets_crypto import encrypt_backup_file


def _database(path):
    conn = sqlite3.connect(path)
    conn.execute('CREATE TABLE example (value TEXT NOT NULL)')
    conn.execute('INSERT INTO example VALUES (?)', ('kept',))
    conn.commit()
    conn.close()


def test_restore_self_contained_encrypted_backup(tmp_path):
    source = tmp_path / 'source.db'
    encrypted = tmp_path / 'backup.db.enc'
    restored = tmp_path / 'restored.db'
    _database(source)
    encrypt_backup_file(source, encrypted, 'restore-passphrase')

    restore(encrypted, restored, 'restore-passphrase')

    conn = sqlite3.connect(restored)
    assert conn.execute('SELECT value FROM example').fetchone()[0] == 'kept'
    conn.close()


def test_restore_never_overwrites_existing_database(tmp_path):
    source = tmp_path / 'invalid.db'
    destination = tmp_path / 'restored.db'
    source.write_bytes(b'not sqlite')
    destination.write_bytes(b'original')

    with pytest.raises(FileExistsError):
        restore(source, destination, None)

    assert destination.read_bytes() == b'original'


def test_restore_rejects_orphaned_wal_sidecar(tmp_path):
    source = tmp_path / 'source.db'
    destination = tmp_path / 'restored.db'
    _database(source)
    destination.with_name(f'{destination.name}-wal').write_bytes(b'stale')

    with pytest.raises(FileExistsError):
        restore(source, destination, None)

    assert not destination.exists()


def test_restore_loses_publication_race_without_overwriting(tmp_path, monkeypatch):
    source = tmp_path / 'source.db'
    destination = tmp_path / 'restored.db'
    _database(source)
    real_link = os.link

    def competing_link(src, dst):
        destination.write_bytes(b'winner')
        return real_link(src, dst)

    monkeypatch.setattr(os, 'link', competing_link)
    with pytest.raises(FileExistsError):
        restore(source, destination, None)

    assert destination.read_bytes() == b'winner'
    assert not list(tmp_path.glob('.restored.db.*.restore'))


def test_restore_refuses_existing_destination(tmp_path):
    source = tmp_path / 'source.db'
    destination = tmp_path / 'restored.db'
    _database(source)
    destination.write_bytes(b'original')

    with pytest.raises(FileExistsError):
        restore(source, destination, None)

    assert destination.read_bytes() == b'original'
