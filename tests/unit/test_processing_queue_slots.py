"""ProcessingQueue as an N-slot registry."""
import multiprocessing
import os
import time

import pytest

_CROSS_PROCESS_TIMEOUT = 5


def _child_hold_slot(ready, release, done):
    """Acquire a slot, signal ready, wait to be told to release, then release and signal done."""
    import processing_queue
    processing_queue.ProcessingQueue._instance = None
    q = processing_queue.ProcessingQueue()
    q.acquire('a', '1', limit=1)
    ready.set()
    release.wait(timeout=_CROSS_PROCESS_TIMEOUT)
    q.release('a', '1')
    done.set()


@pytest.fixture
def queue(temp_dir, monkeypatch):
    monkeypatch.setenv('DATA_DIR', temp_dir)
    import processing_queue
    processing_queue.ProcessingQueue._instance = None
    q = processing_queue.ProcessingQueue()
    yield q
    processing_queue.ProcessingQueue._instance = None


def test_limit_one_matches_old_behavior(queue):
    assert queue.acquire('a', '1', limit=1) is True
    assert queue.acquire('b', '2', limit=1) is False
    assert queue.get_current() == [('a', '1')]
    assert queue.is_processing('a', '1') is True
    queue.release('a', '1')
    assert queue.get_current() == []


def test_multiple_slots_and_release_by_key(queue):
    assert queue.acquire('a', '1', limit=2)
    assert queue.acquire('b', '2', limit=2)
    assert queue.acquire('c', '3', limit=2) is False
    assert queue.get_current() == [('a', '1'), ('b', '2')]
    queue.release('a', '1')
    assert queue.get_current() == [('b', '2')]
    assert queue.acquire('c', '3', limit=2)
    assert queue.is_processing('b', '2') and queue.is_processing('c', '3')


def test_same_episode_twice_is_refused(queue):
    assert queue.acquire('a', '1', limit=3)
    assert queue.acquire('a', '1', limit=3) is False


def test_shrunk_limit_refuses_new_but_keeps_running(queue):
    queue.acquire('a', '1', limit=2)
    queue.acquire('b', '2', limit=2)
    assert queue.acquire('c', '3', limit=1) is False
    assert queue.get_current() == [('a', '1'), ('b', '2')]


def test_stale_slot_from_dead_process_is_cleared_alone(queue, monkeypatch):
    queue.acquire('a', '1', limit=2)
    # A slot left by a process that no longer exists. Mock liveness instead of
    # a fabricated pid: a fixed large pid can collide with a real live process
    # or trip a permission check, depending on the host's pid range.
    dead_pid = os.getpid() + 1
    monkeypatch.setattr(
        'processing_queue.ProcessingQueue._pid_alive',
        staticmethod(lambda pid: pid != dead_pid),
    )
    queue._seed_slot('b', '2', started_at=time.time() - 10, pid=dead_pid)
    assert queue.get_current() == [('a', '1')]


def test_is_processing_false_for_dead_pid_without_rewriting_state(queue, monkeypatch):
    """is_processing takes the cheap read-only path: a dead-pid slot reads as
    not processing, but the state file itself is left untouched (no prune)."""
    dead_pid = os.getpid() + 1
    monkeypatch.setattr(
        'processing_queue.ProcessingQueue._pid_alive',
        staticmethod(lambda pid: pid != dead_pid),
    )
    queue._seed_slot('a', '1', started_at=time.time() - 10, pid=dead_pid)
    before = queue._state_file_path.read_bytes()
    before_mtime = queue._state_file_path.stat().st_mtime_ns

    assert queue.is_processing('a', '1') is False

    assert queue._state_file_path.read_bytes() == before
    assert queue._state_file_path.stat().st_mtime_ns == before_mtime


def test_slot_with_a_recycled_pid_is_pruned(queue):
    """A container restart can hand this pid to a new process, so a slot whose
    recorded process start time no longer matches is dead."""
    import processing_queue
    queue.acquire('a', '1', limit=2)
    stale_start = (processing_queue._pid_start_time(os.getpid()) or 0.0) + 1.0
    queue._seed_slot('b', '2', started_at=time.time() - 10, pid=os.getpid(),
                     pid_start=stale_start)
    assert queue.is_processing('b', '2') is False
    assert queue.get_current() == [('a', '1')]


def test_startup_prune_keeps_slots_that_recorded_a_start_time(queue):
    """A respawned leader has sibling workers still running episodes, so the
    startup prune drops only what liveness cannot judge: slots with no
    recorded start time."""
    queue.acquire('a', '1', limit=3)
    queue._seed_slot('b', '2', started_at=time.time() - 10, pid=os.getpid())
    assert queue.drop_slots_without_start_time() == 1
    assert queue.get_current() == [('a', '1')]


def test_startup_prune_writes_nothing_when_every_slot_has_one(queue):
    queue.acquire('a', '1', limit=2)
    before_mtime = queue._state_file_path.stat().st_mtime_ns
    assert queue.drop_slots_without_start_time() == 0
    assert queue._state_file_path.stat().st_mtime_ns == before_mtime


def test_a_slot_with_no_pid_is_dead(queue):
    queue._seed_slot('a', '1', started_at=time.time(), pid=0)
    assert queue.is_processing('a', '1') is False
    assert queue.get_current() == []


def test_clear_all_drops_every_slot(queue):
    queue.acquire('a', '1', limit=2)
    queue.acquire('b', '2', limit=2)
    assert queue.clear_all() == 2
    assert queue.get_current() == []


def test_hard_timeout_force_clears_only_that_slot(queue, monkeypatch):
    monkeypatch.setattr('processing_queue.get_hard_timeout', lambda: 60)
    queue.acquire('a', '1', limit=2)
    queue._seed_slot('b', '2', started_at=time.time() - 3600, pid=os.getpid())
    assert queue.get_current() == [('a', '1')]


def test_release_if_processing(queue):
    queue.acquire('a', '1', limit=1)
    assert queue.release_if_processing('x', 'y') is False
    assert queue.release_if_processing('a', '1') is True
    assert queue.get_current() == []


def test_cross_process_registry(queue):
    """The state file is the real registry across processes, not per-process
    memory: a slot held by another process must block a same-key acquire and
    a limit=1 acquire here, then free up once that process releases."""
    ctx = multiprocessing.get_context('fork')
    ready = ctx.Event()
    release = ctx.Event()
    done = ctx.Event()
    child = ctx.Process(target=_child_hold_slot, args=(ready, release, done))
    child.start()
    try:
        assert ready.wait(timeout=_CROSS_PROCESS_TIMEOUT), "child did not acquire in time"
        assert queue.is_processing('a', '1') is True
        assert queue.acquire('b', '2', limit=1) is False

        release.set()
        assert done.wait(timeout=_CROSS_PROCESS_TIMEOUT), "child did not release in time"
        child.join(timeout=_CROSS_PROCESS_TIMEOUT)
        assert not child.is_alive()

        assert queue.acquire('b', '2', limit=1) is True
    finally:
        if child.is_alive():
            child.terminate()
            child.join(timeout=_CROSS_PROCESS_TIMEOUT)
