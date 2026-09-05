"""The shared status file survives concurrent writers.

Writers shared one temp filename and only a threading.Lock, so gunicorn
workers corrupted each other's writes and lost each other's updates. These
tests fork to get real cross-process contention.
"""
import json
import multiprocessing
import os

import pytest


@pytest.fixture
def status_service(temp_dir, monkeypatch):
    import status_service as status_service_mod
    monkeypatch.setattr(status_service_mod, '_get_soft_timeout', lambda: 3600)
    status_service_mod.StatusService._instance = None
    monkeypatch.setenv('DATA_DIR', temp_dir)
    ss = status_service_mod.StatusService()
    yield ss
    status_service_mod.StatusService._instance = None


def test_the_lock_file_lands_beside_the_patched_status_file(status_service, temp_dir):
    """A module-level lock constant would still point at the real data dir."""
    status_service.queue_episode('pod', 'ep1', 'Title', 'Pod')

    assert os.path.exists(os.path.join(temp_dir, 'processing_status.json.lock'))


def _queue_in_child(status_path, index):
    """Runs in a separate process, so it gets its own singleton and lock."""
    import os
    import status_service as status_service_mod
    status_service_mod._get_soft_timeout = lambda: 3600
    os.environ['DATA_DIR'] = os.path.dirname(status_path)
    status_service_mod.StatusService._instance = None
    ss = status_service_mod.StatusService()
    for n in range(10):
        ss.queue_episode(f'pod{index}', f'ep{index}-{n}', 'Title', 'Pod')


def test_concurrent_workers_neither_corrupt_nor_lose_updates(temp_dir):
    status_path = os.path.join(temp_dir, 'processing_status.json')
    # spawn, not fork: forked children would inherit this session's open
    # SQLite descriptors, which is exactly what gunicorn.conf.py warns about.
    ctx = multiprocessing.get_context('spawn')

    procs = [ctx.Process(target=_queue_in_child, args=(status_path, i)) for i in range(4)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=60)

    assert all(p.exitcode == 0 for p in procs)
    with open(status_path) as f:
        status = json.load(f)  # corrupt file raises here
    assert len(status['queued_episodes']) == 40
    assert [f for f in os.listdir(temp_dir) if f.endswith('.tmp')] == []
