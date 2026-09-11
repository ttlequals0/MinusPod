"""Dead durable owners recover matching SQLite and display state."""

import sqlite3

import pytest

import processing_queue
from status_service import reconcile_startup_state


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


@pytest.mark.parametrize('queue_status', [None, 'pending', 'processing'])
def test_startup_recovery_restores_dead_run_state(queue_and_status, queue_status):
    pq, ss = queue_and_status
    db = pq._database()
    slug = 'slug-a'
    episode_id = 'ep-1'
    db.upsert_episode(
        slug, episode_id, original_url='https://example.com/episode.mp3',
        title='Episode', status='processing', retry_count=2,
    )
    run_id = _seed_dead(pq, slug, episode_id)
    ss.start_job(slug, episode_id, 'Episode', 'A', run_id=run_id)
    queue_id = None
    if queue_status:
        queue_id = db.queue_episode_for_processing(
            slug, episode_id, 'https://example.com/episode.mp3', 'Episode')
        db.get_connection().execute(
            "UPDATE auto_process_queue SET status = ?, attempts = 2 WHERE id = ?",
            (queue_status, queue_id),
        )
        db.get_connection().commit()

    assert pq.reconcile_dead_owners() == 1
    reconcile_startup_state(db)

    run = db.get_connection().execute(
        'SELECT state FROM processing_runs WHERE run_id = ?', (run_id,),
    ).fetchone()
    episode = db.get_episode(slug, episode_id)
    queue_row = db.get_connection().execute(
        'SELECT * FROM auto_process_queue WHERE id = ?', (queue_id,),
    ).fetchone() if queue_id else None
    assert run['state'] == 'interrupted'
    assert pq.get_current() == []
    assert ss.get_status().current_job is None
    assert episode['status'] == 'pending'
    assert episode['retry_count'] == 2
    assert episode['title'] == 'Episode'
    assert episode['error_message'] == 'Reset after worker crash (no retry penalty)'
    if queue_status:
        assert queue_row['status'] == 'pending'
        assert queue_row['attempts'] == 2
        assert queue_row['original_url'] == 'https://example.com/episode.mp3'
        expected_error = (
            'Reset after worker crash (no attempt penalty)'
            if queue_status == 'processing' else None
        )
        assert queue_row['error_message'] == expected_error
    else:
        assert queue_row is None


def test_live_owner_keeps_processing_state(queue_and_status):
    pq, ss = queue_and_status
    db = pq._database()
    slug = 'slug-a'
    episode_id = 'ep-1'
    db.upsert_episode(
        slug, episode_id, original_url='https://example.com/episode.mp3',
        title='Episode', status='processing', retry_count=2,
    )
    run_id = pq.acquire(slug, episode_id)
    queue_id = db.queue_episode_for_processing(
        slug, episode_id, 'https://example.com/episode.mp3', 'Episode')
    db.get_connection().execute(
        "UPDATE auto_process_queue SET status = 'processing', attempts = 2 WHERE id = ?",
        (queue_id,),
    )
    db.get_connection().commit()
    ss.start_job(slug, episode_id, 'Episode', 'A', run_id=run_id)

    assert pq.reconcile_dead_owners() == 0
    reconcile_startup_state(db)

    assert pq.active_run_id(slug, episode_id) == run_id
    assert db.get_episode(slug, episode_id)['status'] == 'processing'
    assert db.get_queue_row_status(queue_id) == 'processing'
    assert ss.get_status().current_job.run_id == run_id


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


def test_delayed_recovery_cannot_reset_successor(queue_and_status):
    pq, ss = queue_and_status
    db = pq._database()
    db.upsert_episode(
        'slug-a', 'ep-1', original_url='https://example.com/episode.mp3',
        title='Episode', status='processing',
    )
    old_run_id = _seed_dead(pq, 'slug-a', 'ep-1')
    ss.start_job('slug-a', 'ep-1', 'Old', 'Pod', run_id=old_run_id)

    assert pq.reconcile_dead_owners() == 1
    successor_id = pq.acquire('slug-a', 'ep-1')
    db.upsert_episode('slug-a', 'ep-1', status='processing')
    ss.start_job('slug-a', 'ep-1', 'New', 'Pod', run_id=successor_id)

    processing_queue._sync_status_clear('slug-a', 'ep-1', old_run_id)
    reconcile_startup_state(db)

    job = ss.get_status().current_job
    assert job is not None
    assert job.title == 'New'
    assert job.run_id == successor_id
    assert db.get_episode('slug-a', 'ep-1')['status'] == 'processing'


def test_failed_recovery_rolls_back_before_status_cleanup(queue_and_status):
    pq, ss = queue_and_status
    db = pq._database()
    db.upsert_episode(
        'slug-a', 'ep-1', original_url='https://example.com/episode.mp3',
        title='Episode', status='processing',
    )
    run_id = _seed_dead(pq, 'slug-a', 'ep-1')
    ss.start_job('slug-a', 'ep-1', 'Episode', 'A', run_id=run_id)
    conn = db.get_connection()
    conn.execute(
        "CREATE TRIGGER fail_recovery BEFORE UPDATE OF status ON episodes "
        "WHEN NEW.status = 'pending' BEGIN "
        "SELECT RAISE(ABORT, 'recovery failed'); END"
    )
    conn.commit()

    with pytest.raises(sqlite3.IntegrityError, match='recovery failed'):
        pq.reconcile_dead_owners()

    run = conn.execute(
        'SELECT state FROM processing_runs WHERE run_id = ?', (run_id,),
    ).fetchone()
    assert run['state'] == 'running'
    assert db.get_episode('slug-a', 'ep-1')['status'] == 'processing'
    assert ss.get_status().current_job.run_id == run_id
