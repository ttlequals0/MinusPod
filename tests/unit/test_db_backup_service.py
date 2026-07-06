"""Tests for db_backup_service: backup_now, db_backup_tick, validate_backup_dest."""
import fcntl
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from database import Database  # noqa: E402
import db_backup_service  # noqa: E402
from db_backup_service import (  # noqa: E402
    DEFAULT_CRON,
    FIXED_BACKUP_NAME,
    LOCK_FILENAME,
    ROTATED_NAME_RE,
    TEMP_BACKUP_NAME,
    BackupInProgressError,
    backup_now,
    db_backup_tick,
    validate_backup_dest,
)


# chmod-based failure injection is a no-op for root (root bypasses DAC bits),
# so the read-only-parent trick can't force a mkdir failure there. The shipped
# images and CI run as uid 1000; skip rather than report a false failure.
skip_if_root = pytest.mark.skipif(
    os.geteuid() == 0, reason='chmod-based failure injection is a no-op as root'
)


@pytest.fixture
def db(tmp_path):
    Database._instance = None  # type: ignore[attr-defined]
    instance = Database(data_dir=str(tmp_path))
    yield instance
    Database._instance = None  # type: ignore[attr-defined]


def _iso(dt):
    return dt.strftime('%Y-%m-%dT%H:%M:%SZ')


def _read_setting_from_backup(backup_path, key):
    """Open a backup .db and read a settings value, proving the snapshot
    captured live data (not an empty or stale file)."""
    import sqlite3
    conn = sqlite3.connect(str(backup_path))
    try:
        row = conn.execute('SELECT value FROM settings WHERE key = ?', (key,)).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# db_backup_tick gating
# ---------------------------------------------------------------------------

def test_tick_disabled_returns_none(db):
    assert db_backup_tick(db) is None


def test_tick_enabled_last_run_unset_runs(db):
    db.set_setting('db_backup_enabled', 'true')
    summary = db_backup_tick(db)
    assert summary is not None
    assert summary['mode'] == 'overwrite'
    assert db.get_setting('db_backup_last_run')


def test_tick_not_due_skips(db):
    db.set_setting('db_backup_enabled', 'true')
    # Just ran a second ago against the default daily cron -> not due yet.
    db.set_setting('db_backup_last_run', _iso(datetime.now(timezone.utc)))
    assert db_backup_tick(db) is None


def test_tick_due_after_stale_last_run_runs(db):
    db.set_setting('db_backup_enabled', 'true')
    stale = datetime.now(timezone.utc) - timedelta(days=2)
    db.set_setting('db_backup_last_run', _iso(stale))
    summary = db_backup_tick(db)
    assert summary is not None


def test_tick_force_bypasses_disabled(db):
    summary = db_backup_tick(db, force=True)
    assert summary is not None
    assert summary['mode'] == 'overwrite'


# ---------------------------------------------------------------------------
# backup_now: last_run stamping
# ---------------------------------------------------------------------------

@skip_if_root
def test_last_run_stamped_even_on_failure(db, tmp_path):
    # Read-only parent -> snapshot_database's mkdir of the (absent) dest fails.
    parent = tmp_path / 'ro_parent'
    parent.mkdir()
    parent.chmod(0o500)
    dest = parent / 'backups'
    db.set_setting('db_backup_dest', str(dest))
    try:
        with pytest.raises(Exception):
            backup_now(db)
    finally:
        parent.chmod(0o700)
    assert db.get_setting('db_backup_last_run')
    assert db.get_setting('db_backup_last_error')


# ---------------------------------------------------------------------------
# backup_now: overwrite mode (keepCount == 1)
# ---------------------------------------------------------------------------

def test_overwrite_mode_single_file_refreshed(db, tmp_path):
    dest = tmp_path / 'backups'
    db.set_setting('db_backup_dest', str(dest))
    db.set_setting('db_backup_keep_count', '1')

    s1 = backup_now(db)
    assert s1['mode'] == 'overwrite'
    assert s1['keepCount'] == 1
    final = dest / FIXED_BACKUP_NAME
    assert final.exists()
    first_inode = final.stat().st_ino

    # Write a row so the second snapshot differs, proving the file was refreshed.
    db.set_setting('overwrite_probe', 'x' * 1000)

    s2 = backup_now(db)
    files = sorted(p.name for p in dest.iterdir())
    assert files == [FIXED_BACKUP_NAME]
    assert not (dest / TEMP_BACKUP_NAME).exists()
    # os.replace swaps in a fresh inode, and the new snapshot carries the probe.
    assert final.stat().st_ino != first_inode
    probe = _read_setting_from_backup(final, 'overwrite_probe')
    assert probe == 'x' * 1000
    assert s2['prunedCount'] == 0


# ---------------------------------------------------------------------------
# backup_now: rotation (keepCount > 1)
# ---------------------------------------------------------------------------

def test_rotation_keeps_last_n_and_prunes(db, tmp_path, monkeypatch):
    dest = tmp_path / 'backups'
    dest.mkdir()
    db.set_setting('db_backup_dest', str(dest))
    db.set_setting('db_backup_keep_count', '3')

    # Plant decoys that must never be pruned.
    (dest / 'pre-secret-migration-20250101-000000.db').write_text('decoy')
    (dest / 'podcast.db').write_text('decoy')

    for i in range(4):
        # Force distinct UTC-second filenames without real sleeps. monkeypatch
        # restores _utc_now after the test so later tests keep the real clock.
        ts = datetime(2026, 1, 1, 0, 0, i, tzinfo=timezone.utc)
        monkeypatch.setattr(db_backup_service, '_utc_now', lambda ts=ts: ts)
        summary = backup_now(db)

    rotated = sorted(p.name for p in dest.iterdir() if ROTATED_NAME_RE.match(p.name))
    assert len(rotated) == 3
    assert summary['mode'] == 'rotate'
    assert summary['keepCount'] == 3
    assert summary['prunedCount'] == 1

    # Decoys survive.
    assert (dest / 'pre-secret-migration-20250101-000000.db').exists()
    assert (dest / 'podcast.db').exists()
    assert not (dest / TEMP_BACKUP_NAME).exists()


def test_rotation_same_second_keeps_distinct_files(db, tmp_path, monkeypatch):
    # Freeze the clock so two runs land in the same UTC second; the second must
    # not overwrite the first -- both distinct backups survive.
    dest = tmp_path / 'backups'
    dest.mkdir()
    db.set_setting('db_backup_dest', str(dest))
    db.set_setting('db_backup_keep_count', '3')

    frozen = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(db_backup_service, '_utc_now', lambda: frozen)

    s1 = backup_now(db)
    s2 = backup_now(db)
    rotated = sorted(p.name for p in dest.iterdir() if ROTATED_NAME_RE.match(p.name))
    assert len(rotated) == 2
    assert s1['path'] != s2['path']
    assert s2['prunedCount'] == 0


def test_default_dest_rejects_existing_file(db, tmp_path):
    # '' default resolves to <data_dir>/backups; if a file sits there, the
    # not-a-directory guard must fire (not a raw FileExistsError from mkdir).
    (tmp_path / 'backups').write_text('not a dir')
    db.set_setting('db_backup_dest', '')
    with pytest.raises(ValueError):
        backup_now(db)


def test_mode_transition_rotate_to_overwrite(db, tmp_path):
    dest = tmp_path / 'backups'
    dest.mkdir()
    db.set_setting('db_backup_dest', str(dest))

    # Leave rotated files behind from a previous keepCount > 1 run.
    (dest / 'minuspod-backup-20260101-000000.db').write_text('old')
    (dest / 'minuspod-backup-20260101-000001.db').write_text('old')

    db.set_setting('db_backup_keep_count', '1')
    summary = backup_now(db)
    files = sorted(p.name for p in dest.iterdir())
    assert files == [FIXED_BACKUP_NAME]
    assert summary['mode'] == 'overwrite'
    assert summary['prunedCount'] == 2


def test_mode_transition_overwrite_to_rotate(db, tmp_path):
    dest = tmp_path / 'backups'
    dest.mkdir()
    db.set_setting('db_backup_dest', str(dest))

    # Leftover fixed file from a prior overwrite-mode run.
    (dest / FIXED_BACKUP_NAME).write_text('old')

    db.set_setting('db_backup_keep_count', '2')
    summary = backup_now(db)
    assert summary['mode'] == 'rotate'
    # Fixed file removed; one rotated file present.
    assert not (dest / FIXED_BACKUP_NAME).exists()
    rotated = [p.name for p in dest.iterdir() if ROTATED_NAME_RE.match(p.name)]
    assert len(rotated) == 1


# ---------------------------------------------------------------------------
# backup_now: failure stamping and success clearing
# ---------------------------------------------------------------------------

@skip_if_root
def test_failure_stamps_error_then_success_clears(db, tmp_path):
    parent = tmp_path / 'ro_parent'
    parent.mkdir()
    parent.chmod(0o500)
    dest = parent / 'backups'
    db.set_setting('db_backup_dest', str(dest))
    try:
        with pytest.raises(Exception):
            backup_now(db)
    finally:
        parent.chmod(0o700)
    assert db.get_setting('db_backup_last_error')
    # No temp residue after a failed run.
    assert not (dest / TEMP_BACKUP_NAME).exists()

    # A subsequent successful run (dest now writable) clears the error.
    summary = backup_now(db)
    assert summary is not None
    assert db.get_setting('db_backup_last_error') == ''


# ---------------------------------------------------------------------------
# validate_backup_dest
# ---------------------------------------------------------------------------

def test_validate_empty_returns_default(tmp_path):
    result = validate_backup_dest('', str(tmp_path))
    assert result == (tmp_path.resolve() / 'backups')


def test_validate_relative_rejected(tmp_path):
    with pytest.raises(ValueError):
        validate_backup_dest('relative/path', str(tmp_path))


def test_validate_equals_data_dir_rejected(tmp_path):
    with pytest.raises(ValueError):
        validate_backup_dest(str(tmp_path), str(tmp_path))


def test_validate_symlink_to_data_dir_rejected(tmp_path):
    link = tmp_path.parent / (tmp_path.name + '_link')
    os.symlink(str(tmp_path), str(link))
    try:
        with pytest.raises(ValueError):
            validate_backup_dest(str(link), str(tmp_path))
    finally:
        os.unlink(str(link))


def test_validate_existing_file_rejected(tmp_path):
    f = tmp_path / 'afile'
    f.write_text('x')
    with pytest.raises(ValueError):
        validate_backup_dest(str(f), str(tmp_path))


def test_validate_subdir_allowed(tmp_path):
    sub = tmp_path / 'backups'
    result = validate_backup_dest(str(sub), str(tmp_path))
    assert result == sub.resolve()


# ---------------------------------------------------------------------------
# flock contention
# ---------------------------------------------------------------------------

def test_lock_held_raises_backup_in_progress(db, tmp_path):
    db.set_setting('db_backup_dest', str(tmp_path / 'backups'))
    lock_path = db.data_dir / LOCK_FILENAME
    with open(lock_path, 'w') as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BackupInProgressError):
            backup_now(db)
    # last_run is still stamped (stamp happens before the lock attempt).
    assert db.get_setting('db_backup_last_run')
