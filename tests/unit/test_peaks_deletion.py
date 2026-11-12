import os
import shutil
import subprocess
import tempfile
from unittest.mock import patch

import pytest

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
    db.upsert_episode(slug, EP, original_url='https://example.com/a.mp3',
                      title='t', status='processed')
    _seed(storage, slug=slug)
    storage.delete_original_only(slug, EP)
    assert not storage.get_original_path(slug, EP).exists()
    assert not storage.get_peaks_path(slug, EP).exists()


def test_delete_episode_rows_removes_peaks(db_storage, local_feed):
    db, storage = db_storage
    slug = local_feed
    db.upsert_episode(slug, EP, original_url=f'local://{EP}', title='t', status='discovered')
    _seed(storage, slug=slug)
    db.delete_episode_rows(slug, [EP], storage)
    assert not storage.get_peaks_path(slug, EP).exists()


def test_local_import_overwrite_removes_stale_peaks(db_storage, local_feed, real_mp3_bytes):
    db, storage = db_storage
    slug = local_feed
    src_dir = storage.import_source_dir(slug)
    src_dir.mkdir(parents=True)
    audio = src_dir / 'S01E01 - Pilot.mp3'
    audio.write_bytes(real_mp3_bytes)
    plan1 = build_import_plan(slug, [audio], existing_ids=set(),
                              overwrite=False, now_iso=NOW_ISO)
    _commit_synchronously(slug, plan1, db, storage)
    storage.get_peaks_path(slug, 's01e01').write_bytes(b'MPPK' + b'\x00' * 60)

    audio.write_bytes(real_mp3_bytes)
    plan2 = build_import_plan(slug, [audio], existing_ids={'s01e01'},
                              overwrite=True, now_iso=NOW_ISO_2)
    _commit_synchronously(slug, plan2, db, storage)

    assert local_import.get_import_status(slug, storage)['report']['failed'] == []
    assert storage.get_original_path(slug, 's01e01').exists()
    assert not storage.get_peaks_path(slug, 's01e01').exists()


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
