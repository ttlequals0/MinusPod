"""Tests for db_backup_service: backup_now, db_backup_tick, validate_backup_dest."""
import fcntl
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from database import Database  # noqa: E402
import db_backup_service  # noqa: E402
from db_backup_service import (  # noqa: E402
    FIXED_BACKUP_NAME,
    LOCK_FILENAME,
    ROTATED_NAME_RE,
    TEMP_BACKUP_NAME,
    BackupInProgressError,
    backup_now,
    db_backup_tick,
    dest_writable,
    validate_backup_dest,
)
from utils.time import ISO_FORMAT  # noqa: E402


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
    return dt.strftime(ISO_FORMAT)


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


def test_shared_destination_temp_symlink_cannot_redirect_snapshot(db, tmp_path, monkeypatch):
    dest = tmp_path / 'shared'
    dest.mkdir()
    victim = tmp_path / 'unrelated.db'
    with sqlite3.connect(victim) as connection:
        connection.execute('CREATE TABLE sentinel (value TEXT)')
        connection.execute("INSERT INTO sentinel VALUES ('preserve')")
    before = victim.read_bytes()
    db.set_setting('db_backup_dest', str(dest))
    snapshot = db_backup_service.snapshot_database

    def substitute_public_temp(database, path, **kwargs):
        (dest / TEMP_BACKUP_NAME).symlink_to(victim)
        assert path.parent.stat().st_mode & 0o777 == 0o700
        assert path.stat().st_mode & 0o777 == 0o600
        return snapshot(database, path, **kwargs)

    monkeypatch.setattr(db_backup_service, 'snapshot_database', substitute_public_temp)
    backup_now(db)

    assert victim.read_bytes() == before
    assert not (dest / FIXED_BACKUP_NAME).is_symlink()
    assert _read_setting_from_backup(dest / FIXED_BACKUP_NAME, 'db_backup_dest') == str(dest)
    assert not list(dest.glob('.minuspod-backup-*/'))


def test_destination_swap_preserves_prior_backup_and_unrelated_files(db, tmp_path, monkeypatch):
    dest = tmp_path / 'shared'
    db.set_setting('db_backup_dest', str(dest))
    backup_now(db)
    before = (dest / FIXED_BACKUP_NAME).read_bytes()
    moved = tmp_path / 'moved'
    other = tmp_path / 'unrelated'
    other.mkdir()
    sentinel = other / FIXED_BACKUP_NAME
    sentinel.write_text('preserve')
    snapshot = db_backup_service.snapshot_database

    def swap_destination(database, path, **kwargs):
        result = snapshot(database, path, **kwargs)
        dest.rename(moved)
        dest.symlink_to(other, target_is_directory=True)
        return result

    monkeypatch.setattr(db_backup_service, 'snapshot_database', swap_destination)
    with pytest.raises(ValueError, match='destination changed during snapshot'):
        backup_now(db)

    assert (moved / FIXED_BACKUP_NAME).read_bytes() == before
    assert sentinel.read_text() == 'preserve'
    assert list(moved.iterdir()) == [moved / FIXED_BACKUP_NAME]
    assert list(other.iterdir()) == [sentinel]


def test_failed_snapshot_preserves_prior_backup_and_removes_staging(db, tmp_path, monkeypatch):
    dest = tmp_path / 'backups'
    db.set_setting('db_backup_dest', str(dest))
    backup_now(db)
    before = (dest / FIXED_BACKUP_NAME).read_bytes()

    def fail_snapshot(_database, path, **_kwargs):
        path.write_bytes(b'partial')
        raise OSError('snapshot failed')

    monkeypatch.setattr(db_backup_service, 'snapshot_database', fail_snapshot)
    with pytest.raises(OSError, match='snapshot failed'):
        backup_now(db)

    assert (dest / FIXED_BACKUP_NAME).read_bytes() == before
    assert list(dest.iterdir()) == [dest / FIXED_BACKUP_NAME]


@pytest.mark.parametrize('unsafe_stage', ['permissions', 'owner'])
def test_untrusted_staging_preserves_prior_backup_and_closes_descriptors(
        db, tmp_path, monkeypatch, unsafe_stage):
    dest = tmp_path / 'backups'
    db.set_setting('db_backup_dest', str(dest))
    backup_now(db)
    before = (dest / FIXED_BACKUP_NAME).read_bytes()
    mkdtemp = db_backup_service.tempfile.mkdtemp
    fstat = os.fstat
    open_fd = os.open
    stage_inode = None
    opened = []

    def create_untrusted_stage(**kwargs):
        nonlocal stage_inode
        path = mkdtemp(**kwargs)
        stage_inode = os.stat(path).st_ino
        if unsafe_stage == 'permissions':
            os.chmod(path, 0o755)
        return path

    def inspect_stage(fd):
        result = fstat(fd)
        if unsafe_stage == 'owner' and result.st_ino == stage_inode:
            fields = list(result)
            fields[4] = os.geteuid() + 1
            return os.stat_result(fields)
        return result

    def record_open(*args, **kwargs):
        fd = open_fd(*args, **kwargs)
        opened.append(fd)
        return fd

    monkeypatch.setattr(db_backup_service.tempfile, 'mkdtemp', create_untrusted_stage)
    monkeypatch.setattr(os, 'fstat', inspect_stage)
    monkeypatch.setattr(os, 'open', record_open)
    with pytest.raises(ValueError, match='staging directory is not private'):
        backup_now(db)

    assert (dest / FIXED_BACKUP_NAME).read_bytes() == before
    for fd in opened:
        with pytest.raises(OSError):
            fstat(fd)


def test_unavailable_descriptor_paths_preserve_prior_backup(db, tmp_path, monkeypatch):
    dest = tmp_path / 'backups'
    db.set_setting('db_backup_dest', str(dest))
    backup_now(db)
    before = (dest / FIXED_BACKUP_NAME).read_bytes()
    is_dir = db_backup_service.Path.is_dir
    monkeypatch.setattr(db_backup_service.Path, 'is_dir',
                        lambda path: False if str(path) == '/proc/self/fd' else is_dir(path))

    with pytest.raises(RuntimeError, match='Scheduled backups require Linux'):
        backup_now(db)

    assert (dest / FIXED_BACKUP_NAME).read_bytes() == before
    assert list(dest.iterdir()) == [dest / FIXED_BACKUP_NAME]


def test_directory_creation_swap_does_not_chmod_or_write_unrelated_directory(
        db, tmp_path, monkeypatch):
    parent = tmp_path / 'shared'
    parent.mkdir(mode=0o755)
    other = tmp_path / 'unrelated'
    other.mkdir(mode=0o755)
    sentinel = other / FIXED_BACKUP_NAME
    sentinel.write_text('preserve')
    dest = parent / 'backups'
    db.set_setting('db_backup_dest', str(dest))
    mkdir = os.mkdir
    open_fd = os.open
    opened = []

    def swap_new_directory(path, mode=0o777, *, dir_fd=None):
        mkdir(path, mode, dir_fd=dir_fd)
        if path == 'backups':
            os.rename(path, 'moved', src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
            os.symlink(other, path, dir_fd=dir_fd)

    def record_open(*args, **kwargs):
        fd = open_fd(*args, **kwargs)
        opened.append(fd)
        return fd

    monkeypatch.setattr(os, 'mkdir', swap_new_directory)
    monkeypatch.setattr(os, 'open', record_open)
    with pytest.raises(OSError):
        backup_now(db)

    assert other.stat().st_mode & 0o777 == 0o755
    assert parent.stat().st_mode & 0o777 == 0o755
    assert (parent / 'moved').stat().st_mode & 0o777 == 0o700
    assert sentinel.read_text() == 'preserve'
    assert list(other.iterdir()) == [sentinel]
    for fd in opened:
        with pytest.raises(OSError):
            os.fstat(fd)


@pytest.mark.parametrize('keep', [1, 2])
def test_pruning_backup_symlinks_does_not_touch_their_targets(db, tmp_path, keep):
    dest = tmp_path / 'backups'
    dest.mkdir()
    victim = tmp_path / 'unrelated.db'
    victim.write_text('preserve')
    (dest / 'minuspod-backup-auto-20000101-000000.db').symlink_to(victim)
    (dest / 'minuspod-backup-auto-20000101-000001.db').symlink_to(victim)
    (dest / FIXED_BACKUP_NAME).symlink_to(victim)
    db.set_setting('db_backup_dest', str(dest))
    db.set_setting('db_backup_keep_count', str(keep))

    backup_now(db)

    assert victim.read_text() == 'preserve'


def test_overwrite_mode_download_decoy_survives(db, tmp_path):
    # A download named like GET /system/backup (minuspod-backup-<ts>.db) parked
    # in the dest dir must not be pruned in overwrite mode; only the scheduler's
    # -auto- namespace and the fixed file are managed.
    dest = tmp_path / 'backups'
    dest.mkdir()
    db.set_setting('db_backup_dest', str(dest))
    db.set_setting('db_backup_keep_count', '1')

    decoy = dest / 'minuspod-backup-20260101-000000.db'
    decoy.write_text('download')

    summary = backup_now(db)
    assert summary['mode'] == 'overwrite'
    assert summary['prunedCount'] == 0
    assert decoy.exists()
    assert (dest / FIXED_BACKUP_NAME).exists()


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
    # An operator-saved download shares the timestamp shape of downloads
    # (minuspod-backup-<ts>.db) but not the scheduler's -auto- namespace, so it
    # must survive pruning.
    (dest / 'minuspod-backup-20260101-000000.db').write_text('download')

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

    # Decoys survive, including the download-named file.
    assert (dest / 'pre-secret-migration-20250101-000000.db').exists()
    assert (dest / 'podcast.db').exists()
    assert (dest / 'minuspod-backup-20260101-000000.db').exists()
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
    (dest / 'minuspod-backup-auto-20260101-000000.db').write_text('old')
    (dest / 'minuspod-backup-auto-20260101-000001.db').write_text('old')

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
    # last_run is NOT stamped: the contending caller never acquired the lock, so
    # the holder's run keeps the retry slot (stamp happens after lock acquire).
    assert db.get_setting('db_backup_last_run') is None
    # lock contention is not an error-stamping failure.
    assert db.get_setting('db_backup_last_error') is None


def test_tick_under_contention_logs_skip_and_returns_none(db, tmp_path, caplog):
    import logging
    db.set_setting('db_backup_enabled', 'true')
    db.set_setting('db_backup_dest', str(tmp_path / 'backups'))
    lock_path = db.data_dir / LOCK_FILENAME
    with open(lock_path, 'w') as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with caplog.at_level(logging.INFO, logger='podcast.db_backup'):
            result = db_backup_tick(db)
    assert result is None
    assert any('another backup is in progress' in r.message for r in caplog.records)
    # No error stamped: a backup genuinely runs, nothing failed.
    assert db.get_setting('db_backup_last_error') is None


# ---------------------------------------------------------------------------
# Fix 2: directory permission handling
# ---------------------------------------------------------------------------

@skip_if_root
def test_preexisting_dest_dir_keeps_its_mode(db, tmp_path):
    # A destination the operator already created (e.g. a shared mount) keeps its
    # own permissions; backup_now must not chmod it to 0700.
    dest = tmp_path / 'shared'
    dest.mkdir()
    dest.chmod(0o755)
    before = dest.stat().st_mode & 0o777
    db.set_setting('db_backup_dest', str(dest))
    backup_now(db)
    assert (dest.stat().st_mode & 0o777) == before == 0o755


def test_created_dest_dir_gets_0700(db, tmp_path):
    # A destination MinusPod creates itself is locked to 0700.
    dest = tmp_path / 'made' / 'backups'
    db.set_setting('db_backup_dest', str(dest))
    backup_now(db)
    assert dest.exists()
    assert (dest.stat().st_mode & 0o777) == 0o700


# ---------------------------------------------------------------------------
# Fix 3: plaintext WARN parity
# ---------------------------------------------------------------------------

def test_passphrase_set_reports_encrypted_not_exposed(db, tmp_path, monkeypatch, caplog):
    """A configured passphrase means set_secret already encrypted the provider
    keys, so the snapshot is the safe case. This used to warn, which pointed
    operators at the wrong condition."""
    import logging
    monkeypatch.setenv('MINUSPOD_MASTER_PASSPHRASE', 'secret')
    db.set_setting('anthropic_api_key', 'enc:v1:bm9uY2U=:Y3Q=')
    db.set_setting('db_backup_dest', str(tmp_path / 'backups'))
    with caplog.at_level(logging.INFO, logger='podcast.db_backup'):
        backup_now(db)
    assert not [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any('encrypted provider secrets' in r.getMessage() for r in caplog.records)


def test_no_secrets_stored_does_not_warn(db, tmp_path, monkeypatch, caplog):
    """With no passphrase and no provider keys stored there is nothing exposed,
    so the absence of a passphrase alone must not warn."""
    import logging
    monkeypatch.delenv('MINUSPOD_MASTER_PASSPHRASE', raising=False)
    db.set_setting('db_backup_dest', str(tmp_path / 'backups'))
    with caplog.at_level(logging.INFO, logger='podcast.db_backup'):
        backup_now(db)
    assert not [r for r in caplog.records if r.levelno == logging.WARNING]


def test_no_secrets_stored_reports_none_not_encrypted(db, tmp_path, caplog):
    """Zero secret rows means 'has encrypted provider secrets' would be untrue;
    the INFO line must say the snapshot contains none."""
    import logging
    db.set_setting('db_backup_dest', str(tmp_path / 'backups'))
    with caplog.at_level(logging.INFO, logger='podcast.db_backup'):
        backup_now(db)
    assert any('contains no provider secrets' in r.getMessage() for r in caplog.records)
    assert not any('encrypted provider secrets' in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# Fix 8: dest_writable matches mkdir(parents=True)
# ---------------------------------------------------------------------------

def test_dest_writable_nonexistent_two_level_under_writable(tmp_path):
    # Two missing levels under a writable ancestor: backups would succeed.
    target = tmp_path / 'a' / 'b'
    assert dest_writable(target) is True


@skip_if_root
def test_dest_writable_nonexistent_under_unwritable(tmp_path):
    ro = tmp_path / 'ro'
    ro.mkdir()
    ro.chmod(0o500)
    try:
        assert dest_writable(ro / 'child') is False
    finally:
        ro.chmod(0o700)


def test_dest_writable_existing_non_dir(tmp_path):
    f = tmp_path / 'afile'
    f.write_text('x')
    assert dest_writable(f) is False


# ---------------------------------------------------------------------------
# Snapshot secret-state logging. The old condition warned when a passphrase was
# set, which is exactly when the keys are already ciphertext, and stayed silent
# when they were readable.
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_secret_latch():
    db_backup_service.reset_secret_state_log()
    yield
    db_backup_service.reset_secret_state_log()


def test_warns_when_plaintext_secrets_are_present(db, caplog, monkeypatch):
    monkeypatch.setattr(db_backup_service, 'count_plaintext_secrets', lambda _db: 2)
    with caplog.at_level('DEBUG', logger='podcast.db_backup'):
        backup_now(db)
    warnings = [r for r in caplog.records if r.levelname == 'WARNING']
    assert len(warnings) == 1
    assert 'plaintext' in warnings[0].getMessage()


def test_no_warning_when_secrets_are_encrypted(db, caplog):
    db.set_setting('anthropic_api_key', 'enc:v1:bm9uY2U=:Y3Q=')
    with caplog.at_level('DEBUG', logger='podcast.db_backup'):
        backup_now(db)
    assert not [r for r in caplog.records if r.levelname == 'WARNING']
    assert any('encrypted provider secrets' in r.getMessage() for r in caplog.records)


def test_passphrase_alone_does_not_warn(db, caplog, monkeypatch):
    """A configured passphrase is the safe case and must not warn on its own."""
    monkeypatch.setenv('MINUSPOD_MASTER_PASSPHRASE', 'test-passphrase')
    monkeypatch.setattr(db_backup_service, 'count_plaintext_secrets', lambda _db: 0)
    with caplog.at_level('DEBUG', logger='podcast.db_backup'):
        backup_now(db)
    assert not [r for r in caplog.records if r.levelname == 'WARNING']


def test_warning_emitted_at_most_once_per_process(db, caplog, monkeypatch):
    monkeypatch.setattr(db_backup_service, 'count_plaintext_secrets', lambda _db: 1)
    with caplog.at_level('DEBUG', logger='podcast.db_backup'):
        backup_now(db)
        backup_now(db)
    assert len([r for r in caplog.records if r.levelname == 'WARNING']) == 1


def test_secret_count_failure_does_not_break_the_backup(db, caplog, monkeypatch):
    def boom(_db):
        raise RuntimeError('crypto unavailable')

    monkeypatch.setattr(db_backup_service, 'count_plaintext_secrets', boom)
    with caplog.at_level('DEBUG', logger='podcast.db_backup'):
        summary = backup_now(db)
    assert summary['mode'] == 'overwrite'
    assert not [r for r in caplog.records if r.levelname == 'WARNING']
