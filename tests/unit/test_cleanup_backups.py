"""scheduled DB backups respect MINUSPOD_MASTER_PASSPHRASE."""
import os
import sys
import sqlite3
import tempfile

import pytest


@pytest.fixture
def temp_db_with_backup_dir(monkeypatch):
    """Point Database at a fresh tmpdir, yield (db, tmpdir)."""
    tmpdir = tempfile.mkdtemp(prefix='cleanup_backup_test_')
    monkeypatch.setenv('DATA_DIR', tmpdir)

    if os.path.join(os.path.dirname(__file__), '..', '..', 'src') not in sys.path:
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

    import database
    database.Database._instance = None
    database.Database.__init__.__defaults__ = (tmpdir,)
    db = database.Database()
    yield db, tmpdir


def test_plaintext_backup_when_passphrase_unset(monkeypatch, temp_db_with_backup_dir, caplog):
    """Without MINUSPOD_MASTER_PASSPHRASE, produce a .db file + WARN."""
    monkeypatch.delenv('MINUSPOD_MASTER_PASSPHRASE', raising=False)
    import secrets_crypto
    secrets_crypto.reset_cache()

    from cleanup_service import CleanupService
    db, tmpdir = temp_db_with_backup_dir
    svc = CleanupService(db)

    import logging
    with caplog.at_level(logging.WARNING):
        path = svc.backup_database()

    assert path is not None
    assert path.endswith('.db')
    assert not path.endswith('.db.enc')
    assert os.path.getsize(path) > 0
    assert any(
        'UNENCRYPTED' in r.getMessage() for r in caplog.records
    ), "WARN log must mention UNENCRYPTED"

    # File is a valid SQLite database
    conn = sqlite3.connect(path)
    try:
        cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' LIMIT 1")
        assert cur.fetchone() is not None
    finally:
        conn.close()


def test_encrypted_backup_when_passphrase_set(monkeypatch, temp_db_with_backup_dir):
    """With MINUSPOD_MASTER_PASSPHRASE, produce .db.enc file that
    round-trips to valid SQLite via decrypt_bytes."""
    monkeypatch.setenv('MINUSPOD_MASTER_PASSPHRASE', 'scheduled-backup-test-passphrase')
    import secrets_crypto
    secrets_crypto.reset_cache()

    from cleanup_service import CleanupService
    db, tmpdir = temp_db_with_backup_dir
    svc = CleanupService(db)

    path = svc.backup_database()
    assert path is not None
    assert path.endswith('.db.enc'), f"expected .db.enc, got {path}"
    assert os.path.getsize(path) > 0

    # Decryptable and parses as SQLite
    with open(path, 'rb') as f:
        enc = f.read()
    plaintext = secrets_crypto.decrypt_bytes(db, enc)

    decrypted_path = path.replace('.db.enc', '.decrypted.db')
    with open(decrypted_path, 'wb') as f:
        f.write(plaintext)

    conn = sqlite3.connect(decrypted_path)
    try:
        cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' LIMIT 1")
        assert cur.fetchone() is not None
    finally:
        conn.close()


def test_retention_matches_both_extensions(monkeypatch, temp_db_with_backup_dir):
    """_cleanup_old_backups must honour retention across .db and .db.enc."""
    monkeypatch.delenv('MINUSPOD_MASTER_PASSPHRASE', raising=False)
    import secrets_crypto
    secrets_crypto.reset_cache()

    from cleanup_service import CleanupService
    db, tmpdir = temp_db_with_backup_dir

    backup_dir = os.path.join(tmpdir, 'backups')
    os.makedirs(backup_dir, exist_ok=True)

    # Write a mix of .db and .db.enc to test retention
    for i, ext in enumerate(['.db', '.db.enc', '.db', '.db.enc', '.db']):
        p = os.path.join(backup_dir, f'podcast_202604170000{i:02d}{ext}')
        with open(p, 'wb') as f:
            f.write(b'x' * 100)
        os.utime(p, (100 + i, 100 + i))

    svc = CleanupService(db)
    # CleanupService caches settings for 5 min; inject the value directly
    # so the test doesn't depend on cache eviction timing.
    svc._settings_cache.set('_settings', {'backup_keep_count': 3})

    svc._cleanup_old_backups(backup_dir)

    remaining = sorted(os.listdir(backup_dir))
    assert len(remaining) == 3, f"expected 3 remaining, got {remaining}"
