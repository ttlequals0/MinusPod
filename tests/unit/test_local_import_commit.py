"""Tests for the commit half of local_import.py: staging/import-dir moves,
embedded artwork extraction, and the background job registry (Task 10).

Mirrors test_local_episode_api.py's ffmpeg fixture pattern (real tiny mp3s,
skip via requires_ffmpeg) and thread_fakes.SyncThread for deterministic
synchronous execution of the background commit worker.
"""
import os
import shutil
import subprocess
import sys
import tempfile
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
# local_feed_builder.rebuild_local_feed (mocked per-test below) pulls in
# main_app at import time, which -- absent this -- defaults its data dir to
# /app/data and fails to create it outside a container. Matches
# test_local_episode_api.py's identical guard.
os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='local-import-commit-test-'))

# Importing main_app FIRST (before local_feed_builder is ever touched)
# matters: local_feed_builder.py does `from main_app import db, rss_parser,
# storage` at module level, and main_app/feeds.py does
# `from local_feed_builder import rebuild_local_feed` at module level. If
# local_feed_builder is the very first of the two to start importing (e.g.
# via a bare `patch('local_feed_builder.rebuild_local_feed')` in a test),
# that second import hits local_feed_builder mid-initialization and raises
# ImportError. Pre-loading main_app here makes local_feed_builder load as
# one of *its* dependents instead, same as every route-level test does via
# the app_client fixture importing `from main_app import app`.
import main_app  # noqa: E402,F401
import local_import  # noqa: E402
from local_import import build_import_plan  # noqa: E402
from tests.unit.thread_fakes import SyncThread  # noqa: E402

requires_ffmpeg = pytest.mark.skipif(
    shutil.which('ffmpeg') is None or shutil.which('ffprobe') is None,
    reason='ffmpeg/ffprobe not available')

NOW_ISO = '2026-08-27T00:00:00Z'
NOW_ISO_2 = '2026-08-28T00:00:00Z'


@pytest.fixture
def db_storage(tmp_path):
    """Fresh Database + Storage singletons sharing one data dir.

    Storage.__init__ constructs its own Database(str(self.data_dir)), which
    -- since Database is also a singleton -- returns the very instance
    created below rather than a second one, so db and storage.db are the
    same connection.
    """
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
    slug = 'local-import-commit-test'
    db.create_podcast(slug, f'local://{slug}', 'Commit Test', feed_type='local')
    return slug


@pytest.fixture(autouse=True)
def _reset_import_jobs():
    local_import._import_jobs.clear()
    yield
    local_import._import_jobs.clear()


@pytest.fixture(scope='session')
def real_mp3_bytes(tmp_path_factory):
    """A small, structurally valid mp3 -- generated once per test session,
    same command as test_local_episode_api.py's identical fixture."""
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
    """start_commit, forcing the spawned thread to run inline so the job
    is 'done'/'error' by the time this returns."""
    with patch('local_feed_builder.rebuild_local_feed') as mock_rebuild, \
         patch.object(local_import.threading, 'Thread', SyncThread):
        started, reason = local_import.start_commit(slug, plan, db=db, storage=storage)
    return started, reason, mock_rebuild


# ---------------------------------------------------------------------------
# Pure (no ffmpeg) coverage: registry shape, staging/source dir paths,
# free-space refusal, concurrent-start refusal.
# ---------------------------------------------------------------------------

def test_get_import_status_idle_for_unknown_slug():
    assert local_import.get_import_status('never-started') == {
        'state': 'idle', 'processed': 0, 'total': 0, 'startedAt': None,
    }


def test_storage_import_dirs_paths(db_storage):
    _db, storage = db_storage

    staging = storage.import_staging_dir('myshow')
    assert staging == storage.data_dir / 'import-staging' / 'myshow'
    assert not staging.exists()
    created = storage.import_staging_dir('myshow', create=True)
    assert created == staging
    assert created.exists()

    source = storage.import_source_dir('myshow')
    assert source == storage.data_dir / 'import' / 'myshow'
    assert not source.exists()


def test_free_space_refusal(db_storage, local_feed, monkeypatch):
    db, storage = db_storage
    slug = local_feed
    plan = {
        'slug': slug, 'overwrite': False, 'planHash': 'x', 'entries': [],
        'rejected': [],
        'totals': {'importable': 0, 'rejected': 0, 'errors': 0,
                    'bytes': 10_000_000_000},
    }

    class FakeUsage:
        free = 1000

    monkeypatch.setattr(local_import.shutil, 'disk_usage', lambda path: FakeUsage())

    started, reason = local_import.start_commit(slug, plan, db=db, storage=storage)
    assert started is False
    assert reason == 'insufficient free disk space'
    assert local_import.get_import_status(slug)['state'] == 'idle'


def test_concurrent_start_commit_refused(db_storage, local_feed):
    db, storage = db_storage
    slug = local_feed
    plan = {
        'slug': slug, 'overwrite': False, 'planHash': 'x', 'entries': [],
        'rejected': [],
        'totals': {'importable': 0, 'rejected': 0, 'errors': 0, 'bytes': 0},
    }

    local_import._import_jobs[slug] = {
        'state': 'running', 'processed': 0, 'total': 0,
        'startedAt': NOW_ISO, 'report': None,
    }

    started, reason = local_import.start_commit(slug, plan, db=db, storage=storage)
    assert started is False
    assert reason == 'import already running'


# ---------------------------------------------------------------------------
# ffmpeg-backed commit behavior.
# ---------------------------------------------------------------------------

@requires_ffmpeg
def test_extract_embedded_artwork_none_when_no_picture_stream(real_mp3_bytes, tmp_path):
    from utils.audio import extract_embedded_artwork
    mp3_path = tmp_path / 'plain.mp3'
    mp3_path.write_bytes(real_mp3_bytes)
    assert extract_embedded_artwork(str(mp3_path)) is None


@requires_ffmpeg
def test_commit_three_entries_into_empty_feed(db_storage, local_feed, real_mp3_bytes):
    db, storage = db_storage
    slug = local_feed

    src_dir = storage.import_source_dir(slug)
    src_dir.mkdir(parents=True)
    names = ['S01E01 - One.mp3', 'S01E02 - Two.mp3', 'S01E03 - Three.mp3']
    for name in names:
        (src_dir / name).write_bytes(real_mp3_bytes)

    sources = [src_dir / name for name in names]
    plan = build_import_plan(slug, sources, existing_ids=set(),
                             overwrite=False, now_iso=NOW_ISO)
    assert plan['totals']['errors'] == 0
    assert plan['totals']['importable'] == 3

    started, reason, mock_rebuild = _commit_synchronously(slug, plan, db, storage)
    assert started is True
    assert reason == 'started'

    status = local_import.get_import_status(slug)
    assert status['state'] == 'done'
    assert status['processed'] == 3
    assert status['total'] == 3
    report = status['report']
    assert sorted(report['committed']) == ['s01e01', 's01e02', 's01e03']
    assert report['failed'] == []
    assert report['queued'] == []  # empty feed -> initial import never auto-queues

    mock_rebuild.assert_called_once_with(slug)

    for episode_id in ('s01e01', 's01e02', 's01e03'):
        episode = db.get_episode(slug, episode_id)
        assert episode is not None
        assert episode['status'] == 'discovered'
        original_path = storage.get_original_path(slug, episode_id)
        assert original_path.exists()

    # The import-dir audio was consumed by the move.
    for name in names:
        assert not (src_dir / name).exists()


@requires_ffmpeg
def test_second_commit_into_nonempty_feed_with_auto_process_queues(
        db_storage, local_feed, real_mp3_bytes):
    db, storage = db_storage
    slug = local_feed
    src_dir = storage.import_source_dir(slug)
    src_dir.mkdir(parents=True)

    first_audio = src_dir / 'S01E01 - One.mp3'
    first_audio.write_bytes(real_mp3_bytes)
    plan1 = build_import_plan(slug, [first_audio], existing_ids=set(),
                              overwrite=False, now_iso=NOW_ISO)
    started1, _reason1, _mock1 = _commit_synchronously(slug, plan1, db, storage)
    assert started1 is True
    status1 = local_import.get_import_status(slug)
    assert status1['report']['committed'] == ['s01e01']
    assert status1['report']['queued'] == []

    local_import._import_jobs.pop(slug, None)

    second_audio = src_dir / 'S01E02 - Two.mp3'
    second_audio.write_bytes(real_mp3_bytes)
    plan2 = build_import_plan(slug, [second_audio], existing_ids={'s01e01'},
                              overwrite=False, now_iso=NOW_ISO_2)
    started2, _reason2, _mock2 = _commit_synchronously(slug, plan2, db, storage)
    assert started2 is True

    status2 = local_import.get_import_status(slug)
    report2 = status2['report']
    assert report2['committed'] == ['s01e02']
    assert report2['queued'] == ['s01e02']


@requires_ffmpeg
def test_changed_file_after_scan_produces_per_file_error_and_imports_rest(
        db_storage, local_feed, real_mp3_bytes):
    db, storage = db_storage
    slug = local_feed
    src_dir = storage.import_source_dir(slug)
    src_dir.mkdir(parents=True)

    good = src_dir / 'S01E01 - Good.mp3'
    good.write_bytes(real_mp3_bytes)
    changed = src_dir / 'S01E02 - Changed.mp3'
    changed.write_bytes(real_mp3_bytes)

    plan = build_import_plan(slug, [good, changed], existing_ids=set(),
                             overwrite=False, now_iso=NOW_ISO)
    assert plan['totals']['errors'] == 0

    # Mutate the second file's size after the plan snapshot was taken.
    changed.write_bytes(real_mp3_bytes + b'\x00' * 256)

    started, _reason, _mock = _commit_synchronously(slug, plan, db, storage)
    assert started is True

    status = local_import.get_import_status(slug)
    report = status['report']
    assert report['committed'] == ['s01e01']
    assert len(report['failed']) == 1
    assert report['failed'][0] == {
        'episodeId': 's01e02', 'error': 'file changed since scan',
    }

    assert db.get_episode(slug, 's01e01') is not None
    assert db.get_episode(slug, 's01e02') is None
    # Untouched: a failed re-stat must never move the file.
    assert changed.exists()
    assert not storage.get_original_path(slug, 's01e02').exists()


@requires_ffmpeg
def test_overwrite_resets_episode_details(db_storage, local_feed, real_mp3_bytes):
    db, storage = db_storage
    slug = local_feed
    src_dir = storage.import_source_dir(slug)
    src_dir.mkdir(parents=True)

    audio = src_dir / 'S01E01 - Pilot.mp3'
    audio.write_bytes(real_mp3_bytes)
    plan1 = build_import_plan(slug, [audio], existing_ids=set(),
                              overwrite=False, now_iso=NOW_ISO)
    started1, _r1, _m1 = _commit_synchronously(slug, plan1, db, storage)
    assert started1 is True
    assert local_import.get_import_status(slug)['report']['committed'] == ['s01e01']

    # Simulate prior processing output that a full reset must clear.
    storage.save_transcript(slug, 's01e01', 'a previous transcript')
    assert storage.get_transcript(slug, 's01e01') == 'a previous transcript'

    local_import._import_jobs.pop(slug, None)

    audio2 = src_dir / 'S01E01 - Pilot.mp3'
    audio2.write_bytes(real_mp3_bytes)
    plan2 = build_import_plan(slug, [audio2], existing_ids={'s01e01'},
                              overwrite=True, now_iso=NOW_ISO_2)
    assert plan2['entries'][0]['errors'] == []

    started2, _r2, _m2 = _commit_synchronously(slug, plan2, db, storage)
    assert started2 is True

    status2 = local_import.get_import_status(slug)
    assert status2['report']['committed'] == ['s01e01']
    assert status2['report']['failed'] == []
    assert storage.get_transcript(slug, 's01e01') is None
    assert storage.get_original_path(slug, 's01e01').exists()
