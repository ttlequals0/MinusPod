"""Integration tests for feed-delete / cancel job cleanup (#525, #745).

Deleting a podcast cancels any active run for its episodes and deletes the
podcast, its episodes, its queue rows, and its files in the same request.
The podcast delete cascades to processing_runs (ON DELETE CASCADE), so a
running, or wedged and never-acknowledging, worker discovers ownership
loss on its own next cooperative check instead of the delete blocking on
it.
"""
import os
import sys
import tempfile
import threading

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='feed-delete-cancel-'))

pytest.importorskip("ctranslate2", reason="Integration tests require Docker environment")


@pytest.fixture(autouse=True)
def _clean_processing_state():
    """Release the queue and clear the cancel registry around each test."""
    from api import get_status_service
    from processing_queue import ProcessingQueue
    from cancel import _cancel_events, _cancel_events_lock

    def _reset():
        q = ProcessingQueue()
        for slug, episode_id in q.get_current():
            run_id = q.active_run_id(slug, episode_id)
            if run_id and run_id != '__database_unavailable__':
                q.release(run_id, terminal_state='interrupted')
            get_status_service().clear_if_matches(slug, episode_id)
        with _cancel_events_lock:
            _cancel_events.clear()

    _reset()
    yield
    _reset()


def _register_event(run_id):
    from cancel import _cancel_events, _cancel_events_lock
    event = threading.Event()
    with _cancel_events_lock:
        _cancel_events[run_id] = event
    return event


def test_cancel_missing_episode_returns_404(app_client):
    slug, ep = 'gone-feed', 'abcdef012345'
    resp = app_client.post(f'/api/v1/feeds/{slug}/episodes/{ep}/cancel')
    assert resp.status_code == 404


def test_delete_feed_cancels_active_run_and_deletes_immediately(app_client):
    """A podcast with an episode mid-processing is cancelled and deleted in
    one request, with no podcast row, queue row, or active run row left."""
    from api import get_database, get_status_service
    from processing_queue import ProcessingQueue

    db = get_database()
    status_service = get_status_service()
    queue = ProcessingQueue()
    slug, ep = 'active-job-feed', 'ep-active'
    db.create_podcast(slug, 'https://example.com/feed.xml', title='Active Job')
    db.upsert_episode(slug, ep, title='Ep', status='processing')
    db.queue_episode_for_processing(slug, ep, 'https://example.com/ep.mp3', title='Ep')

    run_id = queue.acquire(slug, ep)
    status_service.start_job(slug, ep, 'Ep', 'Active Job', run_id=run_id)
    event = _register_event(run_id)

    resp = app_client.delete(f'/api/v1/feeds/{slug}')

    assert resp.status_code == 200
    body = resp.get_json()
    assert body['cancelledJobs'] == 1
    assert 'cancelled' in body['message'].lower()
    assert event.is_set()
    assert db.get_podcast_by_slug(slug) is None
    assert queue.get_current() == []
    assert queue.active_run_id(slug, ep) is None
    assert status_service.get_status().current_job is None
    remaining_queue_rows = db.get_connection().execute(
        "SELECT COUNT(*) FROM auto_process_queue WHERE episode_id = ?", (ep,)
    ).fetchone()[0]
    assert remaining_queue_rows == 0


def test_delete_feed_does_not_wedge_on_an_unacknowledging_owner(app_client):
    """Deletion must not depend on the owning worker ever checking in: a
    wedged worker's run row is torn down anyway, and its next cooperative
    check aborts cleanly instead of the podcast staying stuck undeletable."""
    from api import get_database, get_status_service
    from processing_queue import ProcessingQueue
    from cancel import ProcessingOwnershipLost, _check_cancel

    db = get_database()
    status_service = get_status_service()
    queue = ProcessingQueue()
    slug, ep = 'no-thread-feed', 'ep-orphan'
    db.create_podcast(slug, 'https://example.com/feed.xml', title='No Thread')
    db.upsert_episode(slug, ep, title='Ep', status='processing')

    run_id = queue.acquire(slug, ep)
    status_service.start_job(slug, ep, 'Ep', 'No Thread', run_id=run_id)

    resp = app_client.delete(f'/api/v1/feeds/{slug}')

    assert resp.status_code == 200
    assert db.get_podcast_by_slug(slug) is None
    assert queue.get_current() == []
    assert status_service.get_status().current_job is None

    # The worker's next cooperative check finds its run gone and aborts.
    with pytest.raises(ProcessingOwnershipLost):
        _check_cancel(None, slug, ep, run_id)
    # Its eventual release() is a harmless no-op, not a crash.
    assert queue.release(run_id, terminal_state='interrupted') is False


def test_delete_feed_waits_for_upload_then_retry_finishes(app_client):
    from api import get_database

    db = get_database()
    slug = 'active-upload-feed'
    db.create_podcast(slug, 'local://active-upload-feed', title='Active Upload',
                      feed_type='local')
    reservation = db.reserve_next_episode_upload(slug, 1)

    waiting = app_client.delete(f'/api/v1/feeds/{slug}')

    assert waiting.status_code == 202
    assert db.get_podcast_by_slug(slug)['deletion_requested_at'] is not None
    assert db.fail_upload_reservation(reservation['id'])

    completed = app_client.delete(f'/api/v1/feeds/{slug}')

    assert completed.status_code == 200
    assert db.get_podcast_by_slug(slug) is None


def test_delete_feed_clears_queued_display_entries(app_client):
    """Deleting a feed drops its queued episodes from the display queue."""
    from api import get_database, get_status_service

    db = get_database()
    status_service = get_status_service()
    slug = 'queued-feed'
    db.create_podcast(slug, 'https://example.com/feed.xml', title='Queued Feed')
    status_service.queue_episode(slug, 'q1', 'Q1', 'Queued Feed')
    status_service.queue_episode(slug, 'q2', 'Q2', 'Queued Feed')
    status_service.queue_episode('other-feed', 'o1', 'O1', 'Other Feed')

    resp = app_client.delete(f'/api/v1/feeds/{slug}')

    assert resp.status_code == 200
    remaining = status_service.get_status().queued_episodes
    assert [(e['slug'], e['episode_id']) for e in remaining] == [('other-feed', 'o1')]

    status_service.remove_queued_episode('other-feed', 'o1')


def test_delete_feed_leaves_unrelated_podcast_untouched(app_client):
    """Cancelling and deleting one podcast's active run must not touch
    another podcast's run, queue rows, or episode rows."""
    from api import get_database
    from processing_queue import ProcessingQueue

    db = get_database()
    queue = ProcessingQueue()
    doomed_slug, doomed_ep = 'doomed-feed', 'ep-doomed'
    safe_slug, safe_ep = 'safe-feed', 'ep-safe'
    db.create_podcast(doomed_slug, 'https://example.com/doomed.xml', title='Doomed')
    db.create_podcast(safe_slug, 'https://example.com/safe.xml', title='Safe')
    db.upsert_episode(doomed_slug, doomed_ep, title='Doomed Ep', status='processing')
    db.upsert_episode(safe_slug, safe_ep, title='Safe Ep', status='processing')
    db.queue_episode_for_processing(
        safe_slug, 'safe-queued', 'https://example.com/safe-queued.mp3', title='Queued')

    doomed_run = queue.acquire(doomed_slug, doomed_ep)
    safe_run = queue.acquire(safe_slug, safe_ep)
    _register_event(doomed_run)
    _register_event(safe_run)

    resp = app_client.delete(f'/api/v1/feeds/{doomed_slug}')

    assert resp.status_code == 200
    assert db.get_podcast_by_slug(doomed_slug) is None
    assert queue.active_run_id(doomed_slug, doomed_ep) is None

    assert db.get_podcast_by_slug(safe_slug) is not None
    assert db.get_episode(safe_slug, safe_ep) is not None
    assert queue.active_run_id(safe_slug, safe_ep) == safe_run
    safe_queue_rows = db.get_connection().execute(
        "SELECT COUNT(*) FROM auto_process_queue WHERE episode_id = 'safe-queued'"
    ).fetchone()[0]
    assert safe_queue_rows == 1

    queue.release(safe_run, terminal_state='interrupted')
    db.delete_podcast(safe_slug)


def test_delete_feed_cleans_up_episode_audio_file(app_client):
    """A podcast's audio file is removed even while its episode is
    mid-processing when the delete request lands (no orphaned audio)."""
    from api import get_database, get_storage
    from processing_queue import ProcessingQueue

    db = get_database()
    storage = get_storage()
    queue = ProcessingQueue()
    slug, ep = 'file-cleanup-feed', 'aaaaaaaaaaaa'
    db.create_podcast(slug, 'https://example.com/feed.xml', title='File Cleanup')
    db.upsert_episode(slug, ep, title='Ep', status='processing')

    audio_path = storage.get_episode_path(slug, ep)
    audio_path.write_bytes(b'fake-audio-bytes')
    assert audio_path.exists()

    run_id = queue.acquire(slug, ep)
    _register_event(run_id)

    resp = app_client.delete(f'/api/v1/feeds/{slug}')

    assert resp.status_code == 200
    assert not audio_path.exists()
    assert not audio_path.parent.parent.exists()  # whole podcast dir is gone


def test_orphan_run_for_missing_podcast_self_terminates_via_api_path(app_client):
    """A processing_runs row left behind for a podcast that no longer
    exists (a delete that outran the FK cascade) is reclaimed on the next
    acquire/reconcile pass instead of permanently occupying a slot."""
    from api import get_database
    from processing_queue import ProcessingQueue

    db = get_database()
    queue = ProcessingQueue()
    slug, ep = 'orphan-source-feed', 'ep-orphan-source'
    db.create_podcast(slug, 'https://example.com/feed.xml', title='Orphan Source')
    db.upsert_episode(slug, ep, title='Ep', status='processing')
    run_id = queue.acquire(slug, ep)
    assert run_id

    conn = db.get_connection()
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("DELETE FROM podcasts WHERE slug = ?", (slug,))
    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")

    assert queue.reconcile_dead_owners() == 0
    row = conn.execute(
        "SELECT state FROM processing_runs WHERE run_id = ?", (run_id,)
    ).fetchone()
    assert row['state'] == 'interrupted'
    assert queue.get_current() == []
