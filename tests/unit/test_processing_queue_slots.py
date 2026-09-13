"""Durable processing-run ownership and capacity tests."""
import multiprocessing
import os
import time
from datetime import datetime, timedelta, timezone

import pytest

_CROSS_PROCESS_TIMEOUT = 5


def _reset_child(data_dir):
    os.environ['DATA_DIR'] = data_dir
    from database import Database
    from processing_queue import ProcessingQueue
    Database._instance = None
    ProcessingQueue._instance = None


def _child_hold_slot(data_dir, ready, release, done):
    _reset_child(data_dir)
    from processing_queue import ProcessingQueue
    queue = ProcessingQueue()
    run_id = queue.acquire('a', '1', limit=1)
    ready.put(run_id)
    release.wait(timeout=_CROSS_PROCESS_TIMEOUT)
    queue.release(run_id)
    done.set()


def _child_abandon_slot(data_dir, ready, leave):
    _reset_child(data_dir)
    from processing_queue import ProcessingQueue
    ready.put(ProcessingQueue().acquire('a', '1', limit=1))
    leave.wait(timeout=_CROSS_PROCESS_TIMEOUT)


def _child_cancel_owner(data_dir, ready, done):
    _reset_child(data_dir)
    from cancel import ProcessingCancelled, _check_cancel
    from processing_queue import ProcessingQueue
    queue = ProcessingQueue()
    run_id = queue.acquire('a', '1', limit=1)
    ready.put(run_id)
    try:
        while True:
            _check_cancel(None, 'a', '1', run_id)
            time.sleep(0.02)
    except ProcessingCancelled:
        queue.release(run_id, terminal_state='interrupted')
        done.set()


def _child_acquire_after_signal(data_dir, start, result):
    _reset_child(data_dir)
    from processing_queue import ProcessingQueue
    start.wait(timeout=_CROSS_PROCESS_TIMEOUT)
    result.put(ProcessingQueue().acquire('a', '1', limit=1))


@pytest.fixture
def queue(temp_dir, monkeypatch):
    monkeypatch.setenv('DATA_DIR', temp_dir)
    from database import Database
    import processing_queue
    Database._instance = None
    processing_queue.ProcessingQueue._instance = None
    db = Database(temp_dir)
    for slug in ('a', 'b', 'c'):
        db.create_podcast(slug, f'https://example.com/{slug}', title=slug)
    yield processing_queue.ProcessingQueue()
    processing_queue.ProcessingQueue._instance = None
    Database._instance = None


def _seed_run(queue, slug, episode_id, pid, pid_start, heartbeat=None,
              state='running', run_id=None):
    db = queue._database()
    podcast_id = db.get_podcast_by_slug(slug)['id']
    run_id = run_id or f'{slug}{episode_id}{pid}'.replace('-', '')
    heartbeat = heartbeat or datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    db.get_connection().execute(
        "INSERT INTO processing_runs "
        "(run_id, podcast_id, episode_id, owner_pid, owner_pid_start, state, heartbeat_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (run_id, podcast_id, episode_id, pid, pid_start, state, heartbeat),
    )
    db.get_connection().commit()
    return run_id


def _row(queue, run_id):
    return queue._database().get_connection().execute(
        "SELECT * FROM processing_runs WHERE run_id = ?", (run_id,)
    ).fetchone()


def test_limit_and_duplicate_episode_are_atomic(queue):
    first = queue.acquire('a', '1', limit=2)
    second = queue.acquire('b', '2', limit=2)
    assert first and second and first != second
    assert queue.acquire('a', '1', limit=3) is None
    assert queue.acquire('c', '3', limit=2) is None
    assert queue.get_current() == [('a', '1'), ('b', '2')]


def test_release_requires_the_exact_owned_run(queue):
    first = queue.acquire('a', '1')
    assert queue.release(first) is True
    successor = queue.acquire('a', '1')
    assert successor != first
    assert queue.release(first) is False
    assert queue.active_run_id('a', '1') == successor


def test_release_is_process_owner_fenced(queue, temp_dir):
    ctx = multiprocessing.get_context('fork')
    ready = ctx.Queue()
    release = ctx.Event()
    done = ctx.Event()
    child = ctx.Process(target=_child_hold_slot,
                        args=(temp_dir, ready, release, done))
    child.start()
    try:
        run_id = ready.get(timeout=_CROSS_PROCESS_TIMEOUT)
        assert run_id
        assert queue.release(run_id) is False
        assert queue.is_processing('a', '1') is True
    finally:
        release.set()
        done.wait(timeout=_CROSS_PROCESS_TIMEOUT)
        child.join(timeout=_CROSS_PROCESS_TIMEOUT)
        if child.is_alive():
            child.kill()
            child.join(timeout=_CROSS_PROCESS_TIMEOUT)


def test_cross_process_capacity_blocks_until_release(queue, temp_dir):
    ctx = multiprocessing.get_context('fork')
    ready = ctx.Queue()
    release = ctx.Event()
    done = ctx.Event()
    child = ctx.Process(target=_child_hold_slot,
                        args=(temp_dir, ready, release, done))
    child.start()
    try:
        assert ready.get(timeout=_CROSS_PROCESS_TIMEOUT)
        assert queue.acquire('b', '2', limit=1) is None
        release.set()
        assert done.wait(timeout=_CROSS_PROCESS_TIMEOUT)
        child.join(timeout=_CROSS_PROCESS_TIMEOUT)
        assert queue.acquire('b', '2', limit=1)
    finally:
        if child.is_alive():
            child.kill()
            child.join(timeout=_CROSS_PROCESS_TIMEOUT)


def test_cross_worker_cancellation_is_polled_and_acknowledged(queue, temp_dir):
    from cancel import request_cancellation, wait_for_cancellation
    ctx = multiprocessing.get_context('fork')
    ready = ctx.Queue()
    done = ctx.Event()
    child = ctx.Process(target=_child_cancel_owner, args=(temp_dir, ready, done))
    child.start()
    try:
        run_id = ready.get(timeout=_CROSS_PROCESS_TIMEOUT)
        assert request_cancellation('a', '1') == run_id
        assert wait_for_cancellation(run_id, _CROSS_PROCESS_TIMEOUT) is True
        assert done.wait(timeout=_CROSS_PROCESS_TIMEOUT)
        child.join(timeout=_CROSS_PROCESS_TIMEOUT)
        assert _row(queue, run_id)['state'] == 'interrupted'
    finally:
        if child.is_alive():
            child.kill()
            child.join(timeout=_CROSS_PROCESS_TIMEOUT)


def test_live_owner_survives_stale_heartbeat(queue, monkeypatch):
    import processing_queue
    monkeypatch.setattr(processing_queue, 'get_soft_timeout', lambda: 1)
    heartbeat = (datetime.now(timezone.utc) - timedelta(hours=2)).strftime(
        '%Y-%m-%dT%H:%M:%SZ')
    run_id = _seed_run(
        queue, 'a', '1', os.getpid(), processing_queue._pid_start_time(os.getpid()),
        heartbeat=heartbeat)
    assert queue.get_current() == [('a', '1')]
    assert _row(queue, run_id)['state'] == 'running'


def test_recycled_process_identity_is_reclaimed(queue, monkeypatch):
    import processing_queue
    run_id = _seed_run(queue, 'a', '1', os.getpid(), 1.0)
    monkeypatch.setattr(processing_queue, '_pid_start_time', lambda pid: 2.0)
    assert queue.reconcile_dead_owners() == 1
    assert queue.get_current() == []
    assert _row(queue, run_id)['state'] == 'interrupted'


def test_dead_owner_is_reclaimed_after_real_process_exit(queue, temp_dir):
    ctx = multiprocessing.get_context('fork')
    ready = ctx.Queue()
    leave = ctx.Event()
    child = ctx.Process(target=_child_abandon_slot, args=(temp_dir, ready, leave))
    child.start()
    run_id = ready.get(timeout=_CROSS_PROCESS_TIMEOUT)
    leave.set()
    child.join(timeout=_CROSS_PROCESS_TIMEOUT)
    assert not child.is_alive()
    assert queue.reconcile_dead_owners() == 1
    assert queue.get_current() == []
    assert _row(queue, run_id)['state'] == 'interrupted'


def test_corrupt_legacy_json_has_no_authority_and_is_removed(queue):
    queue._legacy_state_path.write_text('{corrupt')
    run_id = queue.acquire('a', '1')
    assert run_id
    assert queue.drop_slots_without_start_time() == 1
    assert not queue._legacy_state_path.exists()
    assert queue.active_run_id('a', '1') == run_id


def test_database_failures_fail_closed(queue, monkeypatch):
    def unavailable():
        raise RuntimeError('database unavailable')

    monkeypatch.setattr(queue, '_database', unavailable)
    assert queue.acquire('a', '1') is None
    assert queue.is_processing('a', '1') is True
    assert queue.slot_count() == 2 ** 31 - 1


def test_cancel_request_does_not_release_capacity(queue):
    from cancel import request_cancellation, wait_for_cancellation
    run_id = queue.acquire('a', '1')
    assert request_cancellation('a', '1') == run_id
    assert wait_for_cancellation(run_id, timeout=0) is False
    assert queue.acquire('b', '2', limit=1) is None
    assert queue.release(run_id, terminal_state='interrupted') is True


def test_pause_committed_before_admission_blocks_cross_process_acquire(
        queue, temp_dir):
    from processing_queue import set_processing_paused

    ctx = multiprocessing.get_context('fork')
    start = ctx.Event()
    result = ctx.Queue()
    child = ctx.Process(
        target=_child_acquire_after_signal, args=(temp_dir, start, result))
    child.start()
    try:
        set_processing_paused(True, queue._database())
        start.set()
        assert result.get(timeout=_CROSS_PROCESS_TIMEOUT) is None
        child.join(timeout=_CROSS_PROCESS_TIMEOUT)
        assert not child.is_alive()
        assert queue.get_current() == []
    finally:
        if child.is_alive():
            child.kill()
            child.join(timeout=_CROSS_PROCESS_TIMEOUT)


def test_zombie_is_not_considered_live(queue, monkeypatch):
    import processing_queue
    monkeypatch.setattr(processing_queue, '_pid_stat', lambda pid: (10.0, 'Z'))
    assert queue._pid_alive(os.getpid()) is False
