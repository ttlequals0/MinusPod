import os
import shutil
import subprocess
import tempfile
import time
from unittest.mock import patch

import numpy as np
import pytest

import audio_peaks as ap

os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='peaks-deletion-test-'))

import main_app  # noqa: E402,F401
import local_import  # noqa: E402
from local_import import build_import_plan  # noqa: E402
from tests.unit.thread_fakes import SyncThread  # noqa: E402

NOW_ISO = '2026-08-27T00:00:00Z'
NOW_ISO_2 = '2026-08-28T00:00:00Z'


@pytest.fixture
def db_storage(tmp_path):
    from database import Database
    from storage import Storage

    Database._instance = None
    Storage._instance = None
    db = Database(data_dir=str(tmp_path))
    storage = Storage(str(tmp_path))
    yield db, storage
    Database._instance = None
    Storage._instance = None


@pytest.fixture
def local_feed(db_storage):
    db, _storage = db_storage
    slug = 'peaks-local-feed'
    db.create_podcast(slug, f'local://{slug}', 'Peaks Test', feed_type='local')
    return slug


@pytest.fixture(scope='session')
def real_mp3_bytes(tmp_path_factory):
    if shutil.which('ffmpeg') is None:
        pytest.skip('ffmpeg not available')
    mp3_path = tmp_path_factory.mktemp('audio') / 'fixture.mp3'
    subprocess.run(
        ['ffmpeg', '-y', '-f', 'lavfi', '-i', 'anullsrc=r=44100:cl=mono',
         '-t', '1', '-q:a', '9', str(mp3_path)],
        check=True, capture_output=True,
    )
    return mp3_path.read_bytes()


def _commit_synchronously(slug, plan, db, storage):
    with patch('local_feed_builder.rebuild_local_feed'), \
         patch.object(local_import.threading, 'Thread', SyncThread):
        return local_import.start_commit(slug, plan, db=db, storage=storage)


SLUG = 'peaks-del-feed'
EP = 'a1b2c3d4e5f6'


@pytest.fixture
def storage(db_storage):
    return db_storage[1]


def _seed(storage, slug=SLUG, episode_id=EP):
    original = storage.get_original_path(slug, episode_id)
    original.write_bytes(b'\x00' * 1024)
    storage.get_peaks_path(slug, episode_id).write_bytes(b'MPPK' + b'\x00' * 60)
    return original


def test_peaks_path_is_sibling_of_original(storage):
    original = storage.get_original_path(SLUG, EP)
    assert storage.get_peaks_path(SLUG, EP) == original.with_suffix('.peaks')


def test_delete_peaks_file_reports_removal_and_tolerates_missing(storage):
    _seed(storage)
    assert storage.delete_peaks_file(SLUG, EP) is True
    assert storage.delete_peaks_file(SLUG, EP) is False


def test_delete_peaks_file_only_touches_its_own_episode(storage):
    _seed(storage)
    other = _seed(storage, episode_id='b1b2c3d4e5f6')
    storage.delete_peaks_file(SLUG, EP)
    assert storage.get_peaks_path(SLUG, 'b1b2c3d4e5f6').exists()
    assert other.exists()


def test_delete_peaks_file_swallows_os_errors(storage):
    _seed(storage)
    peaks = storage.get_peaks_path(SLUG, EP)
    peaks.unlink()
    peaks.mkdir()  # unlink on a directory raises an OSError that is not FileNotFoundError
    assert storage.delete_peaks_file(SLUG, EP) is False


def test_delete_original_only_removes_peaks(storage):
    _seed(storage)
    storage.delete_original_only(SLUG, EP)
    assert not storage.get_peaks_path(SLUG, EP).exists()


def test_delete_original_only_removes_peaks_left_without_original(storage):
    original = _seed(storage)
    original.unlink()
    assert storage.delete_original_only(SLUG, EP) == (False, 0)
    assert not storage.get_peaks_path(SLUG, EP).exists()


def test_delete_processed_file_removes_peaks_when_not_keeping_original(storage):
    _seed(storage)
    storage.delete_processed_file(SLUG, EP, keep_original=False)
    assert not storage.get_peaks_path(SLUG, EP).exists()


def test_delete_processed_file_keep_original_keeps_peaks(storage):
    original = _seed(storage)
    storage.delete_processed_file(SLUG, EP, keep_original=True)
    assert storage.get_peaks_path(SLUG, EP).exists()
    assert original.exists()


def test_delete_processed_file_fence_blocks_peaks_removal(storage):
    _seed(storage)
    storage.delete_processed_file(SLUG, EP, keep_original=False, can_delete=lambda: False)
    assert storage.get_peaks_path(SLUG, EP).exists()


def test_cleanup_episode_files_removes_peaks(storage):
    original = _seed(storage)
    storage.cleanup_episode_files(SLUG, EP)
    assert not original.exists()
    assert not storage.get_peaks_path(SLUG, EP).exists()


def test_cleanup_podcast_dir_removes_peaks(storage):
    _seed(storage)
    storage.cleanup_podcast_dir(SLUG)
    assert not storage.get_peaks_path(SLUG, EP).exists()


def test_retention_sweep_removes_peaks_with_original(db_storage):
    db, storage = db_storage
    slug = 'peaks-sweep-feed'
    db.create_podcast(slug, 'https://example.com/feed.xml', 'Sweep Test')
    db.set_setting('retention_days', '30')
    db.set_setting('original_retention_days', '5')
    db.upsert_episode(slug, EP, original_url='https://example.com/a.mp3',
                      title='t', status='processed', processed_file='a.mp3')
    conn = db.get_connection()
    conn.execute("UPDATE episodes SET processed_at = datetime('now', '-10 days') "
                 "WHERE episode_id = ?", (EP,))
    conn.commit()
    original = _seed(storage, slug=slug)

    db.cleanup_old_episodes(storage=storage)

    assert not original.exists()
    assert not storage.get_peaks_path(slug, EP).exists()


def test_delete_processed_file_fence_stop_after_peaks_keeps_original_not_orphan_peaks(storage):
    original = _seed(storage)
    answers = iter([True, False])
    storage.delete_processed_file(SLUG, EP, keep_original=False,
                                  can_delete=lambda: next(answers))
    assert not storage.get_peaks_path(SLUG, EP).exists()
    assert original.exists()


def test_delete_processed_file_return_value_ignores_peaks_only(storage):
    original = _seed(storage)
    original.unlink()
    assert storage.delete_processed_file(SLUG, EP, keep_original=False) is False
    assert not storage.get_peaks_path(SLUG, EP).exists()


def test_delete_peaks_file_with_hostile_id_returns_false(storage, tmp_path):
    _seed(storage)
    outside = tmp_path.parent / 'peaks-hostile-outside.peaks'
    outside.write_bytes(b'x')
    outside_stat = outside.stat()
    watched = [storage.get_original_path(SLUG, EP).parent, tmp_path]

    def snapshot():
        return {str(f): f.stat().st_mtime_ns for d in watched for f in d.rglob('*') if f.is_file()}
    before = snapshot()
    for hostile in ('../../etc/passwd', '../peaks-hostile-outside', '..'):
        assert storage.delete_peaks_file(SLUG, hostile) is False
    assert snapshot() == before
    assert outside.read_bytes() == b'x'
    assert outside.stat().st_mtime_ns == outside_stat.st_mtime_ns
    outside.unlink()


def test_cleanup_episode_files_counts_peaks_bytes(storage):
    _seed(storage)
    peaks_size = storage.get_peaks_path(SLUG, EP).stat().st_size
    assert storage.cleanup_episode_files(SLUG, EP) == 1024 + peaks_size


def test_delete_episode_rows_removes_peaks(db_storage, local_feed):
    db, storage = db_storage
    slug = local_feed
    db.upsert_episode(slug, EP, original_url=f'local://{EP}', title='t', status='discovered')
    _seed(storage, slug=slug)
    db.delete_episode_rows(slug, [EP], storage)
    assert not storage.get_peaks_path(slug, EP).exists()


def _stamp_for(path, original):
    """Write a real peaks file stamped for the current bytes of original."""
    ap.write_peaks_file(path, np.array([1, 2, 3], dtype='<u2'), original)


def test_local_import_overwrite_leaves_the_stamp_to_catch_the_stale_peaks(db_storage, local_feed, real_mp3_bytes):
    db, storage = db_storage
    slug = local_feed
    src_dir = storage.import_source_dir(slug)
    src_dir.mkdir(parents=True)
    audio = src_dir / 'S01E01 - Pilot.mp3'
    audio.write_bytes(real_mp3_bytes)
    plan1 = build_import_plan(slug, [audio], existing_ids=set(),
                              overwrite=False, now_iso=NOW_ISO)
    _commit_synchronously(slug, plan1, db, storage)
    _stamp_for(storage.get_peaks_path(slug, 's01e01'), storage.get_original_path(slug, 's01e01'))

    audio.write_bytes(real_mp3_bytes + b'\x00' * 64)
    plan2 = build_import_plan(slug, [audio], existing_ids={'s01e01'},
                              overwrite=True, now_iso=NOW_ISO_2)
    _commit_synchronously(slug, plan2, db, storage)

    assert local_import.get_import_status(slug, storage)['report']['failed'] == []
    original = storage.get_original_path(slug, 's01e01')
    assert original.exists()
    assert not ap.validate_or_restamp_peaks_file(storage.get_peaks_path(slug, 's01e01'), original)


def test_local_import_wide_id_replacement_removes_old_peaks(db_storage, local_feed, real_mp3_bytes):
    db, storage = db_storage
    slug = local_feed
    wide_id = 's01e0006'
    db.upsert_episode(slug, wide_id, original_url=f'local://{wide_id}',
                      status='discovered', title='Old', season_number=1,
                      episode_number=6, published_at=NOW_ISO, original_duration=1.0)
    storage.get_original_path(slug, wide_id).write_bytes(real_mp3_bytes)
    old_peaks = storage.get_peaks_path(slug, wide_id)
    old_peaks.write_bytes(b'MPPK' + b'\x00' * 60)

    src_dir = storage.import_source_dir(slug)
    src_dir.mkdir(parents=True)
    audio = src_dir / 'S01E06 - Pilot.mp3'
    audio.write_bytes(real_mp3_bytes)
    plan = build_import_plan(slug, [audio], existing_ids={wide_id},
                             overwrite=True, now_iso=NOW_ISO_2)
    _commit_synchronously(slug, plan, db, storage)

    assert not old_peaks.exists()


def _temp_files(storage, episode_id=EP):
    peaks = storage.get_peaks_path(SLUG, episode_id)
    mine = peaks.with_name(peaks.name + '.abcd1234.tmp')
    mine.write_bytes(b'x')
    return mine


def _age(path, seconds=7200):
    old = time.time() - seconds
    os.utime(path, (old, old))


def test_delete_peaks_file_removes_its_lock_and_never_scans_the_directory(storage, monkeypatch):
    _seed(storage)
    _seed(storage, episode_id='b1b2c3d4e5f6')
    theirs = _temp_files(storage, 'b1b2c3d4e5f6')
    lock = storage.get_peaks_path(SLUG, EP).with_name('a1b2c3d4e5f6-original.peaks.lock')
    lock.write_bytes(b'')

    def no_scan(*_a, **_k):
        raise AssertionError('per-episode delete scanned a directory')

    monkeypatch.setattr(os, 'scandir', no_scan)
    assert storage.delete_peaks_file(SLUG, EP) is True
    assert not lock.exists() and not storage.get_peaks_path(SLUG, EP).exists()
    assert theirs.exists()


def test_delete_peaks_file_without_a_peaks_file_returns_false(storage):
    assert storage.delete_peaks_file(SLUG, EP) is False


def test_sweep_removes_stale_temp_files_and_keeps_young_ones(storage):
    _seed(storage)
    _seed(storage, episode_id='b1b2c3d4e5f6')
    stale = _temp_files(storage)
    young = _temp_files(storage, 'b1b2c3d4e5f6')
    _age(stale)
    assert storage.sweep_peaks_temp_files() == 1
    assert not stale.exists() and young.exists()
    assert storage.get_peaks_path(SLUG, EP).exists()


def test_delete_processed_file_fence_keeps_the_peaks_file(storage):
    _seed(storage)
    storage.delete_processed_file(SLUG, EP, keep_original=False, can_delete=lambda: False)
    assert storage.get_peaks_path(SLUG, EP).exists()


def test_storage_stats_do_not_count_peaks_temp_files(storage):
    storage.get_original_path(SLUG, EP).write_bytes(b'\x00' * 100)
    before = storage.get_storage_stats()
    _temp_files(storage).write_bytes(b'x' * 5000)
    storage._storage_stats_cache = type(storage._storage_stats_cache)(0)
    after = storage.get_storage_stats()
    assert after['total_size_bytes'] == before['total_size_bytes']
    assert after['file_count'] == before['file_count']


def _recovery_row(operation, slug, target_key, temp_name, backup_name=None):
    return {'id': 'r1', 'state': 'publishing', 'operation': operation, 'scope': 'episode',
            'slug': slug, 'target_key': target_key, 'temp_name': temp_name,
            'backup_name': backup_name, 'backup_target_name': None}


def _recovery_db(tmp_path):
    from database import Database
    return Database(data_dir=str(tmp_path))


def test_upload_recovery_moving_the_original_away_leaves_no_valid_peaks(db_storage, tmp_path):
    db, storage = db_storage
    original = storage.get_original_path(SLUG, EP)
    original.write_bytes(b'new bytes')
    peaks = storage.get_peaks_path(SLUG, EP)
    _stamp_for(peaks, original)
    temp_rel = 'import/local/temp.mp3'
    (tmp_path / 'import' / 'local').mkdir(parents=True)
    row = _recovery_row('import', SLUG, EP, temp_rel)
    assert db._recover_upload_files(row) is True
    assert not original.exists() and (tmp_path / temp_rel).exists()
    assert not ap.validate_or_restamp_peaks_file(peaks, original)


def test_upload_recovery_restoring_a_backup_leaves_no_valid_peaks(db_storage, tmp_path):
    db, storage = db_storage
    original = storage.get_original_path(SLUG, EP)
    original.write_bytes(b'new bytes')
    peaks = storage.get_peaks_path(SLUG, EP)
    _stamp_for(peaks, original)
    backup = original.with_name('.upload-recovery.backup')
    backup.write_bytes(b'old bytes, a different length')
    row = _recovery_row('import', SLUG, EP, None, str(backup.relative_to(tmp_path)))
    assert db._recover_upload_files(row) is True
    assert original.read_bytes() == b'old bytes, a different length'
    assert not ap.validate_or_restamp_peaks_file(peaks, original)


def test_upload_recovery_without_a_move_leaves_peaks(db_storage, tmp_path):
    db, storage = db_storage
    peaks = storage.get_peaks_path(SLUG, EP)
    peaks.write_bytes(b'MPPK' + b'\x00' * 60)
    row = _recovery_row('import', SLUG, EP, 'import/local/none.mp3')
    assert db._recover_upload_files(row) is True
    assert peaks.exists()


def test_delete_processed_file_removes_the_build_lock(storage):
    _seed(storage)
    lock = storage.get_peaks_path(SLUG, EP).with_name(f'{EP}-original.peaks.lock')
    lock.write_bytes(b'')
    storage.delete_processed_file(SLUG, EP, keep_original=False)
    assert not lock.exists()


def test_delete_processed_file_keep_original_keeps_the_build_lock(storage):
    _seed(storage)
    lock = storage.get_peaks_path(SLUG, EP).with_name(f'{EP}-original.peaks.lock')
    lock.write_bytes(b'')
    storage.delete_processed_file(SLUG, EP, keep_original=True)
    assert lock.exists()


def test_storage_stats_do_not_count_the_lock_but_count_the_peaks_file(storage):
    _seed(storage)
    before = storage.get_storage_stats()
    lock = storage.get_peaks_path(SLUG, EP).with_name(f'{EP}-original.peaks.lock')
    lock.write_bytes(b'')
    storage._storage_stats_cache = type(storage._storage_stats_cache)(0)
    after = storage.get_storage_stats()
    assert after['file_count'] == before['file_count'] == 2
