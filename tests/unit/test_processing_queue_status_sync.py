"""Orphan clear in ProcessingQueue should also clear StatusService current_job."""
import os
import time

import pytest


@pytest.fixture
def queue_and_status(temp_dir, monkeypatch):
    monkeypatch.setenv('DATA_DIR', temp_dir)
    # Force fresh singletons bound to temp_dir.
    import processing_queue
    import status_service
    processing_queue.ProcessingQueue._instance = None
    status_service.StatusService._instance = None
    # Rebind STATUS_FILE for this test run.
    monkeypatch.setattr(
        status_service, 'STATUS_FILE',
        os.path.join(temp_dir, 'processing_status.json'),
    )
    pq = processing_queue.ProcessingQueue()
    ss = status_service.StatusService()
    yield pq, ss
    processing_queue.ProcessingQueue._instance = None
    status_service.StatusService._instance = None


def test_orphan_clear_also_clears_matching_status(queue_and_status):
    pq, ss = queue_and_status
    ss.start_job('slug-a', 'ep-1', 'Title', 'Pod')
    # Seed queue state with no live flock holder -> orphaned.
    pq._write_state('slug-a', 'ep-1', time.time() - 10)

    cleared = pq._clear_stale_state()

    assert cleared is True
    assert ss.get_status().current_job is None


def test_orphan_clear_leaves_unmatched_status_alone(queue_and_status):
    pq, ss = queue_and_status
    ss.start_job('slug-b', 'ep-2', 'Other', 'Pod')
    pq._write_state('slug-a', 'ep-1', time.time() - 10)

    pq._clear_stale_state()

    job = ss.get_status().current_job
    assert job is not None
    assert job.slug == 'slug-b'
