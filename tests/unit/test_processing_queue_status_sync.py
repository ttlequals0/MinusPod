"""Dead durable owners also clear their matching display status."""

import pytest


@pytest.fixture
def queue_and_status(temp_dir, monkeypatch):
    monkeypatch.setenv('DATA_DIR', temp_dir)
    # Force fresh singletons bound to temp_dir.
    import processing_queue
    import status_service
    from database import Database
    Database._instance = None
    processing_queue.ProcessingQueue._instance = None
    status_service.StatusService._instance = None
    pq = processing_queue.ProcessingQueue()
    ss = status_service.StatusService()
    db = Database(temp_dir)
    db.create_podcast('slug-a', 'https://example.com/a', title='A')
    db.create_podcast('slug-b', 'https://example.com/b', title='B')
    yield pq, ss
    processing_queue.ProcessingQueue._instance = None
    status_service.StatusService._instance = None
    Database._instance = None


def _seed_dead(pq, slug, episode_id):
    db = pq._database()
    podcast_id = db.get_podcast_by_slug(slug)['id']
    db.get_connection().execute(
        "INSERT INTO processing_runs "
        "(run_id, podcast_id, episode_id, owner_pid, state) "
        "VALUES (?, ?, ?, ?, 'running')",
        (f'{slug}:{episode_id}', podcast_id, episode_id, 2 ** 22),
    )
    db.get_connection().commit()
    return f'{slug}:{episode_id}'


def test_orphan_clear_also_clears_matching_status(queue_and_status):
    pq, ss = queue_and_status
    run_id = _seed_dead(pq, 'slug-a', 'ep-1')
    ss.start_job('slug-a', 'ep-1', 'Title', 'Pod', run_id=run_id)

    assert pq.reconcile_dead_owners() == 1
    assert ss.get_status().current_job is None


def test_orphan_clear_leaves_unmatched_status_alone(queue_and_status):
    pq, ss = queue_and_status
    ss.start_job('slug-b', 'ep-2', 'Other', 'Pod', run_id='other-run')
    _seed_dead(pq, 'slug-a', 'ep-1')

    pq.reconcile_dead_owners()

    job = ss.get_status().current_job
    assert job is not None
    assert job.slug == 'slug-b'


def test_stale_recovery_cannot_clear_successor_status(queue_and_status):
    pq, ss = queue_and_status
    old_run_id = _seed_dead(pq, 'slug-a', 'ep-1')
    ss.start_job('slug-a', 'ep-1', 'Old', 'Pod', run_id=old_run_id)

    assert pq.reconcile_dead_owners() == 1
    successor_id = pq.acquire('slug-a', 'ep-1')
    ss.start_job('slug-a', 'ep-1', 'New', 'Pod', run_id=successor_id)

    import processing_queue
    processing_queue._sync_status_clear('slug-a', 'ep-1', old_run_id)

    job = ss.get_status().current_job
    assert job is not None
    assert job.title == 'New'
    assert job.run_id == successor_id
