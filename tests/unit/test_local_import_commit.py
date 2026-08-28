"""Tests for the commit half of local_import.py: staging/import-dir moves,
embedded artwork extraction, and the cross-worker (file + flock backed) job
state (Task 10, and the field-test cross-worker fix).

Mirrors test_local_episode_api.py's ffmpeg fixture pattern (real tiny mp3s,
skip via requires_ffmpeg) and thread_fakes.SyncThread for deterministic
synchronous execution of the background commit worker.
"""
import json
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

# Smallest valid JPEG magic-number prefix (mirrors test_episode_artwork_cache.py).
JPEG_BYTES = b'\xff\xd8\xff\xe0' + b'\x00' * 64


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


def _commit_synchronously(slug, plan, db, storage, rebuild_side_effect=None):
    """start_commit, forcing the spawned thread to run inline so the job
    is 'done'/'error' by the time this returns."""
    with patch('local_feed_builder.rebuild_local_feed',
               side_effect=rebuild_side_effect) as mock_rebuild, \
         patch.object(local_import.threading, 'Thread', SyncThread):
        started, reason = local_import.start_commit(slug, plan, db=db, storage=storage)
    return started, reason, mock_rebuild


def _committed_ids(report):
    return sorted(item['episodeId'] for item in report['committed'])


# ---------------------------------------------------------------------------
# Pure (no ffmpeg) coverage: job state shape, staging/source dir paths,
# free-space refusal, concurrent-start refusal, slug mismatch, thread-start
# failure.
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
    assert local_import.get_import_status(slug, storage)['state'] == 'idle'


def test_concurrent_start_commit_refused(db_storage, local_feed):
    """Two start_commits racing for the same feed: the second's flock
    acquire fails while the first (simulated here by holding the lock
    directly) is still in flight -- exactly one wins."""
    db, storage = db_storage
    slug = local_feed
    plan = {
        'slug': slug, 'overwrite': False, 'planHash': 'x', 'entries': [],
        'rejected': [],
        'totals': {'importable': 0, 'rejected': 0, 'errors': 0, 'bytes': 0},
    }

    lock_fh = local_import._try_acquire_import_lock(storage, slug)
    assert lock_fh is not None
    try:
        started, reason = local_import.start_commit(slug, plan, db=db, storage=storage)
        assert started is False
        assert reason == 'import already running'
    finally:
        local_import._release_import_lock(lock_fh)


def test_start_commit_refuses_plan_slug_mismatch(db_storage, local_feed):
    db, storage = db_storage
    slug = local_feed
    plan = {
        'slug': 'a-totally-different-slug', 'overwrite': False, 'planHash': 'x',
        'entries': [], 'rejected': [],
        'totals': {'importable': 0, 'rejected': 0, 'errors': 0, 'bytes': 0},
    }

    started, reason = local_import.start_commit(slug, plan, db=db, storage=storage)
    assert started is False
    assert reason == 'plan slug mismatch'
    assert local_import.get_import_status(slug, storage)['state'] == 'idle'


def test_start_commit_clears_registry_when_thread_start_fails(db_storage, local_feed):
    db, storage = db_storage
    slug = local_feed
    plan = {
        'slug': slug, 'overwrite': False, 'planHash': 'x', 'entries': [],
        'rejected': [],
        'totals': {'importable': 0, 'rejected': 0, 'errors': 0, 'bytes': 0},
    }

    class ExplodingThread:
        def __init__(self, target=None, args=(), daemon=None):
            pass

        def start(self):
            raise RuntimeError('cannot start thread')

    with patch.object(local_import.threading, 'Thread', ExplodingThread):
        with pytest.raises(RuntimeError):
            local_import.start_commit(slug, plan, db=db, storage=storage)

    # No phantom 'running' job left behind that would block every future
    # start_commit for this feed.
    assert local_import.get_import_status(slug, storage)['state'] == 'idle'
    # And the lock itself was released too -- not just the state file --
    # otherwise a future start_commit would be refused forever even though
    # status reads idle.
    lock_fh = local_import._try_acquire_import_lock(storage, slug)
    assert lock_fh is not None
    local_import._release_import_lock(lock_fh)


def test_get_import_status_reads_directly_from_file_no_registry(db_storage, local_feed):
    """Status must be readable straight from the state file -- there is no
    in-process registry backing it, so a job state written by hand (as a
    stand-in for "some other worker process wrote this") is visible exactly
    as if start_commit itself had run in this process."""
    db, storage = db_storage
    slug = local_feed

    jobs_dir = storage.data_dir / '.import-jobs'
    jobs_dir.mkdir(parents=True, exist_ok=True)
    report = {'committed': [{'episodeId': 's01e01', 'audioFile': 'a.mp3', 'warnings': []}],
              'skipped': [], 'failed': [], 'queued': []}
    (jobs_dir / f'{slug}.json').write_text(json.dumps({
        'state': 'done', 'processed': 1, 'total': 1,
        'startedAt': NOW_ISO, 'report': report,
    }))

    status = local_import.get_import_status(slug, storage)
    assert status['state'] == 'done'
    assert status['processed'] == 1
    assert status['total'] == 1
    assert status['report'] == report


def test_stale_running_state_with_free_lock_reports_interrupted(db_storage, local_feed):
    """A worker that crashed mid-commit (e.g. OOM-killed) leaves the state
    file stuck on 'running' but the OS releases its flock -- get_import_status
    must self-heal that into 'error' rather than claiming the import is
    still going forever."""
    db, storage = db_storage
    slug = local_feed

    jobs_dir = storage.data_dir / '.import-jobs'
    jobs_dir.mkdir(parents=True, exist_ok=True)
    (jobs_dir / f'{slug}.json').write_text(json.dumps({
        'state': 'running', 'processed': 1, 'total': 3,
        'startedAt': NOW_ISO, 'report': None,
    }))
    # No lock held for this slug -- the orphaned-state case.

    status = local_import.get_import_status(slug, storage)
    assert status['state'] == 'error'
    assert status['report']['error'] == 'import interrupted'

    # Self-healed: a second read (or a future start_commit) doesn't need to
    # re-derive it.
    status2 = local_import.get_import_status(slug, storage)
    assert status2['state'] == 'error'


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

    status = local_import.get_import_status(slug, storage)
    assert status['state'] == 'done'
    assert status['processed'] == 3
    assert status['total'] == 3
    report = status['report']
    assert _committed_ids(report) == ['s01e01', 's01e02', 's01e03']
    # Every outcome record carries audioFile so a UI can map it to a file.
    for item in report['committed']:
        assert item['audioFile'] in names
    assert report['failed'] == []
    assert report['queued'] == []  # empty feed -> initial import never auto-queues

    mock_rebuild.assert_called_once_with(slug)

    for episode_id in ('s01e01', 's01e02', 's01e03'):
        episode = db.get_episode(slug, episode_id)
        assert episode is not None
        assert episode['status'] == 'discovered'
        original_path = storage.get_original_path(slug, episode_id)
        assert original_path.exists()
        # Report bug 1: original_file must be set on commit so
        # hasOriginalAudio is true immediately, not just after the first
        # processing run happens to write the column.
        assert episode['original_file'] == f'{episode_id}-original.mp3'

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
    status1 = local_import.get_import_status(slug, storage)
    assert _committed_ids(status1['report']) == ['s01e01']
    assert status1['report']['queued'] == []


    second_audio = src_dir / 'S01E02 - Two.mp3'
    second_audio.write_bytes(real_mp3_bytes)
    plan2 = build_import_plan(slug, [second_audio], existing_ids={'s01e01'},
                              overwrite=False, now_iso=NOW_ISO_2)
    started2, _reason2, _mock2 = _commit_synchronously(slug, plan2, db, storage)
    assert started2 is True

    status2 = local_import.get_import_status(slug, storage)
    report2 = status2['report']
    assert _committed_ids(report2) == ['s01e02']
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

    status = local_import.get_import_status(slug, storage)
    report = status['report']
    assert _committed_ids(report) == ['s01e01']
    assert len(report['failed']) == 1
    assert report['failed'][0] == {
        'episodeId': 's01e02', 'audioFile': 'S01E02 - Changed.mp3',
        'error': 'file changed since scan',
    }

    assert db.get_episode(slug, 's01e01') is not None
    assert db.get_episode(slug, 's01e02') is None
    # Untouched: a failed re-stat must never move the file.
    assert changed.exists()
    assert not storage.get_original_path(slug, 's01e02').exists()


@requires_ffmpeg
def test_mtime_only_change_after_scan_produces_per_file_error(
        db_storage, local_feed, real_mp3_bytes):
    """Same size, different mtime -- a size-only re-stat would miss this."""
    db, storage = db_storage
    slug = local_feed
    src_dir = storage.import_source_dir(slug)
    src_dir.mkdir(parents=True)

    audio = src_dir / 'S01E01 - Pilot.mp3'
    audio.write_bytes(real_mp3_bytes)
    plan = build_import_plan(slug, [audio], existing_ids=set(),
                             overwrite=False, now_iso=NOW_ISO)
    assert plan['entries'][0]['errors'] == []
    original_mtime_ns = plan['entries'][0]['mtimeNs']

    # Rewrite the mtime only (same bytes, same size), far enough back that
    # even 1-second-resolution filesystems land in a different bucket.
    new_mtime_ns = original_mtime_ns - 5_000_000_000
    os.utime(audio, ns=(new_mtime_ns, new_mtime_ns))
    assert audio.stat().st_mtime_ns != original_mtime_ns
    assert audio.stat().st_size == plan['entries'][0]['bytes']

    started, _reason, _mock = _commit_synchronously(slug, plan, db, storage)
    assert started is True

    report = local_import.get_import_status(slug, storage)['report']
    assert report['committed'] == []
    assert report['failed'][0]['error'] == 'file changed since scan'
    assert db.get_episode(slug, 's01e01') is None
    assert audio.exists()


@requires_ffmpeg
def test_overwrite_clears_processing_columns_and_artwork(
        db_storage, local_feed, real_mp3_bytes):
    """Critical fix: overwrite must be a full reset. A stale new_duration,
    ads_removed, processed_version, error_message, ad_detection_status, and
    cached artwork from a previous processing run must not survive into the
    re-imported episode -- otherwise the rebuilt RSS advertises the
    previous audio's duration."""
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

    # Simulate a completed processing run's leftovers on this episode.
    db.upsert_episode(
        slug, 's01e01',
        status='processed', new_duration=42.0, ads_removed=7,
        ads_removed_firstpass=4, ads_removed_secondpass=3,
        processed_version=2, error_message='a previous failure',
        ad_detection_status='completed', processed_file='s01e01-v2.mp3',
    )
    storage.save_episode_artwork(slug, 's01e01', JPEG_BYTES, 'image/jpeg', evict=False)
    assert storage.has_episode_artwork(slug, 's01e01')


    audio2 = src_dir / 'S01E01 - Pilot.mp3'
    audio2.write_bytes(real_mp3_bytes)
    plan2 = build_import_plan(slug, [audio2], existing_ids={'s01e01'},
                              overwrite=True, now_iso=NOW_ISO_2)
    assert plan2['entries'][0]['errors'] == []

    started2, _r2, _m2 = _commit_synchronously(slug, plan2, db, storage)
    assert started2 is True
    report2 = local_import.get_import_status(slug, storage)['report']
    assert report2['failed'] == []
    assert _committed_ids(report2) == ['s01e01']

    episode = db.get_episode(slug, 's01e01')
    assert episode['status'] == 'discovered'
    assert episode['new_duration'] is None
    assert episode['ads_removed'] == 0
    assert episode['ads_removed_firstpass'] == 0
    assert episode['ads_removed_secondpass'] == 0
    assert episode['processed_version'] == 0
    assert episode['error_message'] is None
    assert episode['ad_detection_status'] is None
    assert episode['processed_file'] is None
    # A fresh probe of the ~1s fixture, not the stale 42.0.
    assert episode['original_duration'] < 5.0
    assert not storage.has_episode_artwork(slug, 's01e01')
    assert storage.get_original_path(slug, 's01e01').exists()


@requires_ffmpeg
def test_overwrite_false_refuses_when_episode_created_after_plan(
        db_storage, local_feed, real_mp3_bytes):
    """Critical fix: the destructive reset must be gated on plan['overwrite'],
    not row existence. A concurrent single-episode upload landing between
    the plan scan and the commit must never be clobbered by a plan that was
    built (and approved by the operator) with overwrite=False."""
    db, storage = db_storage
    slug = local_feed
    src_dir = storage.import_source_dir(slug)
    src_dir.mkdir(parents=True)

    audio = src_dir / 'S01E01 - Pilot.mp3'
    audio.write_bytes(real_mp3_bytes)
    plan = build_import_plan(slug, [audio], existing_ids=set(),
                             overwrite=False, now_iso=NOW_ISO)
    assert plan['entries'][0]['errors'] == []

    # Simulate the concurrent creation: nothing existed when the plan was
    # scanned, but the episode exists by the time commit actually runs.
    db.upsert_episode(slug, 's01e01', status='discovered',
                      original_url='local://s01e01', title='Concurrent Upload')

    started, _reason, _mock = _commit_synchronously(slug, plan, db, storage)
    assert started is True

    report = local_import.get_import_status(slug, storage)['report']
    assert report['committed'] == []
    assert len(report['failed']) == 1
    assert report['failed'][0]['episodeId'] == 's01e01'
    assert report['failed'][0]['error'] == 'episode s01e01 already exists'

    # Never clobbered: the concurrently-created row survives untouched, and
    # the plan's audio file was never moved.
    assert db.get_episode(slug, 's01e01')['title'] == 'Concurrent Upload'
    assert audio.exists()
    assert not storage.get_original_path(slug, 's01e01').exists()


@requires_ffmpeg
def test_overwrite_skips_when_episode_is_processing(
        db_storage, local_feed, real_mp3_bytes):
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

    db.upsert_episode(slug, 's01e01', status='processing')

    audio2 = src_dir / 'S01E01 - Pilot.mp3'
    audio2.write_bytes(real_mp3_bytes)
    plan2 = build_import_plan(slug, [audio2], existing_ids={'s01e01'},
                              overwrite=True, now_iso=NOW_ISO_2)

    started2, _r2, _m2 = _commit_synchronously(slug, plan2, db, storage)
    assert started2 is True

    report2 = local_import.get_import_status(slug, storage)['report']
    assert report2['committed'] == []
    assert report2['failed'][0]['error'] == 'episode is processing'
    assert db.get_episode(slug, 's01e01')['status'] == 'processing'
    # Untouched: the file mid-processing must never be replaced.
    assert audio2.exists()


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
    assert _committed_ids(local_import.get_import_status(slug, storage)['report']) == ['s01e01']

    # Simulate prior processing output that a full reset must clear.
    storage.save_transcript(slug, 's01e01', 'a previous transcript')
    assert storage.get_transcript(slug, 's01e01') == 'a previous transcript'


    audio2 = src_dir / 'S01E01 - Pilot.mp3'
    audio2.write_bytes(real_mp3_bytes)
    plan2 = build_import_plan(slug, [audio2], existing_ids={'s01e01'},
                              overwrite=True, now_iso=NOW_ISO_2)
    assert plan2['entries'][0]['errors'] == []

    started2, _r2, _m2 = _commit_synchronously(slug, plan2, db, storage)
    assert started2 is True

    status2 = local_import.get_import_status(slug, storage)
    assert _committed_ids(status2['report']) == ['s01e01']
    assert status2['report']['failed'] == []
    assert storage.get_transcript(slug, 's01e01') is None
    assert storage.get_original_path(slug, 's01e01').exists()


@requires_ffmpeg
def test_commit_uses_plan_resolved_path_not_basename_lookup(
        db_storage, local_feed, real_mp3_bytes):
    """Important fix: a same-named file sitting in the OTHER scanned
    directory must never cause the wrong file to be committed. The commit
    engine must use the plan's own resolved path, not a basename lookup
    across staging + the import dir."""
    db, storage = db_storage
    slug = local_feed
    import_dir = storage.import_source_dir(slug)
    import_dir.mkdir(parents=True)
    staging_dir = storage.import_staging_dir(slug, create=True)

    name = 'S01E01 - Pilot.mp3'
    import_bytes = real_mp3_bytes
    staging_bytes = real_mp3_bytes + b'\x00' * 512  # distinguishable by length
    (import_dir / name).write_bytes(import_bytes)
    (staging_dir / name).write_bytes(staging_bytes)

    # Plan built ONLY from the import dir -- the same-named staging file
    # must never be picked up, even though basename resolution alone would
    # be ambiguous.
    plan = build_import_plan(slug, [import_dir / name], existing_ids=set(),
                             overwrite=False, now_iso=NOW_ISO)
    assert plan['entries'][0]['bytes'] == len(import_bytes)

    started, _reason, _mock = _commit_synchronously(slug, plan, db, storage)
    assert started is True
    assert local_import.get_import_status(slug, storage)['report']['failed'] == []

    committed_path = storage.get_original_path(slug, 's01e01')
    # The wrong (staging) bytes were never used as the source -- the plan's
    # own resolved import_dir path won.
    assert committed_path.read_bytes() == import_bytes

    # The staging file was irrelevant to THIS plan, but a finished commit
    # sweeps the whole staging directory regardless (operator ruling,
    # Fix 11) -- it does not survive just because this plan never
    # referenced it.
    assert not staging_dir.exists()


@requires_ffmpeg
def test_staged_audio_commits_and_cleans_up_staging(db_storage, local_feed, real_mp3_bytes):
    """Staged audio commits normally, its staging-dir sidecars are deleted
    after commit, and the now-empty staging dir is removed."""
    db, storage = db_storage
    slug = local_feed
    staging_dir = storage.import_staging_dir(slug, create=True)

    name = 'S01E01 - Pilot'
    (staging_dir / f'{name}.mp3').write_bytes(real_mp3_bytes)
    (staging_dir / f'{name}.txt').write_text('a description', encoding='utf-8')
    (staging_dir / f'{name}.jpg').write_bytes(JPEG_BYTES)

    sources = [staging_dir / f'{name}.mp3', staging_dir / f'{name}.txt',
              staging_dir / f'{name}.jpg']
    plan = build_import_plan(slug, sources, existing_ids=set(),
                             overwrite=False, now_iso=NOW_ISO)
    assert plan['entries'][0]['errors'] == []

    started, _reason, _mock = _commit_synchronously(slug, plan, db, storage)
    assert started is True
    report = local_import.get_import_status(slug, storage)['report']
    assert report['failed'] == []
    assert _committed_ids(report) == ['s01e01']

    episode = db.get_episode(slug, 's01e01')
    assert episode['description'] == 'a description'
    assert storage.has_episode_artwork(slug, 's01e01')

    # Staging is fully cleaned up: audio was moved, sidecars were deleted,
    # and the now-empty staging dir itself was removed.
    assert not (staging_dir / f'{name}.mp3').exists()
    assert not (staging_dir / f'{name}.txt').exists()
    assert not (staging_dir / f'{name}.jpg').exists()
    assert not staging_dir.exists()


@requires_ffmpeg
def test_import_dir_sidecars_consumed_on_successful_commit(db_storage, local_feed, real_mp3_bytes):
    """Operator ruling: a successfully committed entry's sidecars are
    consumed regardless of source -- the user-managed import dir is no
    longer special-cased to leave them behind."""
    db, storage = db_storage
    slug = local_feed
    src_dir = storage.import_source_dir(slug)
    src_dir.mkdir(parents=True)

    name = 'S01E01 - Pilot'
    (src_dir / f'{name}.mp3').write_bytes(real_mp3_bytes)
    (src_dir / f'{name}.txt').write_text('a description', encoding='utf-8')
    (src_dir / f'{name}.jpg').write_bytes(JPEG_BYTES)

    sources = [src_dir / f'{name}.mp3', src_dir / f'{name}.txt',
              src_dir / f'{name}.jpg']
    plan = build_import_plan(slug, sources, existing_ids=set(),
                             overwrite=False, now_iso=NOW_ISO)

    started, _reason, _mock = _commit_synchronously(slug, plan, db, storage)
    assert started is True
    assert local_import.get_import_status(slug, storage)['report']['failed'] == []

    assert not (src_dir / f'{name}.mp3').exists()
    assert not (src_dir / f'{name}.txt').exists()
    assert not (src_dir / f'{name}.jpg').exists()


def test_import_dir_sidecars_of_rejected_entry_survive_commit(
        db_storage, local_feed, real_mp3_bytes):
    """A rejected/errored entry is never committed, so its import-dir
    sidecars must be left in place for the operator to fix and re-scan --
    only a SUCCESSFULLY committed entry's sidecars get consumed."""
    db, storage = db_storage
    slug = local_feed
    src_dir = storage.import_source_dir(slug)
    src_dir.mkdir(parents=True)

    # Bad naming scheme -> rejected outright (no episode id), sidecar has no
    # matching audio it can be committed under.
    (src_dir / 'not-a-valid-name.mp3').write_bytes(real_mp3_bytes)
    (src_dir / 'not-a-valid-name.txt').write_text('desc', encoding='utf-8')

    sources = [src_dir / 'not-a-valid-name.mp3', src_dir / 'not-a-valid-name.txt']
    plan = build_import_plan(slug, sources, existing_ids=set(),
                             overwrite=False, now_iso=NOW_ISO)
    assert plan['totals']['importable'] == 0

    started, _reason, _mock = _commit_synchronously(slug, plan, db, storage)
    assert started is True

    # Rejected before ever reaching _commit_entry: nothing moved, nothing
    # deleted.
    assert (src_dir / 'not-a-valid-name.mp3').exists()
    assert (src_dir / 'not-a-valid-name.txt').exists()


@requires_ffmpeg
def test_staging_swept_after_commit_even_with_rejected_files_present(
        db_storage, local_feed, real_mp3_bytes):
    """Operator ruling: a finished commit clears everything left in
    staging, rejected files and skipped entries included, and removes the
    directory itself -- not just the entries that were part of this plan."""
    db, storage = db_storage
    slug = local_feed
    staging_dir = storage.import_staging_dir(slug, create=True)

    good_name = 'S01E01 - Pilot'
    (staging_dir / f'{good_name}.mp3').write_bytes(real_mp3_bytes)
    # A rejected file (bad naming scheme) that will still be sitting in
    # staging when the commit finishes.
    (staging_dir / 'stray-file.wav').write_bytes(b'\x00' * 16)

    sources = [staging_dir / f'{good_name}.mp3', staging_dir / 'stray-file.wav']
    plan = build_import_plan(slug, sources, existing_ids=set(),
                             overwrite=False, now_iso=NOW_ISO)
    assert plan['totals']['rejected'] == 1

    started, _reason, _mock = _commit_synchronously(slug, plan, db, storage)
    assert started is True
    report = local_import.get_import_status(slug, storage)['report']
    assert _committed_ids(report) == ['s01e01']

    # Everything is gone -- the committed audio (moved) AND the rejected
    # leftover -- and so is the directory itself.
    assert not staging_dir.exists()


@requires_ffmpeg
def test_invalid_sidecar_artwork_falls_back_to_embedded_with_warning(
        db_storage, local_feed, real_mp3_bytes):
    db, storage = db_storage
    slug = local_feed
    src_dir = storage.import_source_dir(slug)
    src_dir.mkdir(parents=True)

    name = 'S01E01 - Pilot'
    (src_dir / f'{name}.mp3').write_bytes(real_mp3_bytes)
    (src_dir / f'{name}.jpg').write_bytes(b'not actually an image')

    sources = [src_dir / f'{name}.mp3', src_dir / f'{name}.jpg']
    plan = build_import_plan(slug, sources, existing_ids=set(),
                             overwrite=False, now_iso=NOW_ISO)
    assert plan['entries'][0]['artworkFile'] == f'{name}.jpg'

    started, _reason, _mock = _commit_synchronously(slug, plan, db, storage)
    assert started is True
    report = local_import.get_import_status(slug, storage)['report']
    assert report['failed'] == []
    assert len(report['committed']) == 1
    warnings = report['committed'][0]['warnings']
    assert any('invalid' in w for w in warnings)
    # No embedded picture stream either (the fixture mp3 has none), so no
    # artwork ends up saved -- the warning is what tells the operator why.
    assert not storage.has_episode_artwork(slug, 's01e01')


@requires_ffmpeg
def test_error_state_report_still_exposes_outcomes_before_crash(
        db_storage, local_feed, real_mp3_bytes):
    """Important fix: a crash outside the per-entry loop (here, the RSS
    rebuild step) must not swallow the outcomes already recorded for the
    entries that DID commit before the crash."""
    db, storage = db_storage
    slug = local_feed
    src_dir = storage.import_source_dir(slug)
    src_dir.mkdir(parents=True)
    audio = src_dir / 'S01E01 - Pilot.mp3'
    audio.write_bytes(real_mp3_bytes)
    plan = build_import_plan(slug, [audio], existing_ids=set(),
                             overwrite=False, now_iso=NOW_ISO)

    started, _reason, _mock = _commit_synchronously(
        slug, plan, db, storage, rebuild_side_effect=RuntimeError('boom'))
    assert started is True

    status = local_import.get_import_status(slug, storage)
    assert status['state'] == 'error'
    report = status['report']
    assert report['error'] == 'boom'
    assert len(report['committed']) == 1
    assert report['committed'][0]['episodeId'] == 's01e01'
    # The entry itself really did commit despite the later crash.
    assert db.get_episode(slug, 's01e01') is not None
