"""ProcessingQueue as an N-slot registry."""
import os
import time

import pytest


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
    # A slot left by a process that no longer exists.
    queue._seed_slot('b', '2', started_at=time.time() - 10, pid=2 ** 22)
    assert queue.get_current() == [('a', '1')]


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
