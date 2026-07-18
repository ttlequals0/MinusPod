"""Integration tests for feed-delete / cancel job cleanup (#525).

Deleting a feed while one of its episodes is processing used to leave the
ProcessingQueue lock, the cancel-event registry, and the in-memory status
display dangling, and the cancel endpoint then 404'd because the episode row
was cascade-deleted. These tests cover the belt-and-suspenders fix:
  - delete_feed cancels/cleans in-flight and queued jobs before deleting.
  - the cancel endpoint tears down an orphaned job (200) instead of 404-ing
    when the episode row is already gone.
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
    from processing_queue import ProcessingQueue
    from cancel import _cancel_events, _cancel_events_lock

    def _reset():
        ProcessingQueue().release()
        with _cancel_events_lock:
            _cancel_events.clear()

    _reset()
    yield
    _reset()


def _register_event(slug, episode_id):
    from cancel import _cancel_events, _cancel_events_lock
    event = threading.Event()
    with _cancel_events_lock:
        _cancel_events[f'{slug}:{episode_id}'] = event
    return event


def test_cancel_missing_episode_returns_200_not_404(app_client):
    """Cancelling an episode whose row is gone tears down the job (200)."""
    from processing_queue import ProcessingQueue

    # episode_id must be 12-char hex: the API now 400s malformed ids at the
    # URL layer (is_valid_episode_id) before the route body runs.
    slug, ep = 'gone-feed', 'abcdef012345'
    ProcessingQueue().acquire(slug, ep)
    event = _register_event(slug, ep)

    resp = app_client.post(f'/api/v1/feeds/{slug}/episodes/{ep}/cancel')

    assert resp.status_code == 200
    assert event.is_set()
    assert ProcessingQueue().get_current() is None


def test_delete_feed_signals_active_job_and_clears_status(app_client):
    """Deleting a feed with a local processing thread signals cancel and clears
    the display, but leaves the lock for the signalled thread to release itself."""
    from api import get_database, get_status_service
    from processing_queue import ProcessingQueue

    db = get_database()
    status_service = get_status_service()
    slug, ep = 'active-job-feed', 'ep-active'
    db.create_podcast(slug, 'https://example.com/feed.xml', title='Active Job')
    db.upsert_episode(slug, ep, title='Ep', status='processing')

    ProcessingQueue().acquire(slug, ep)
    status_service.start_job(slug, ep, 'Ep', 'Active Job')
    event = _register_event(slug, ep)

    resp = app_client.delete(f'/api/v1/feeds/{slug}')

    assert resp.status_code == 200
    assert event.is_set()                                     # thread was told to abort
    assert status_service.get_status().current_job is None    # display cleared
    # Lock is NOT force-released here: the signalled thread owns it and clears
    # state on exit. Forcing a release would false-idle a live cross-worker job.
    assert ProcessingQueue().get_current() == (slug, ep)


def test_delete_feed_force_releases_when_no_local_thread(app_client):
    """With no local cancel event (thread in another worker or already gone),
    delete_feed force-releases the lock so it does not dangle for the dead feed."""
    from api import get_database, get_status_service
    from processing_queue import ProcessingQueue

    db = get_database()
    status_service = get_status_service()
    slug, ep = 'no-thread-feed', 'ep-orphan'
    db.create_podcast(slug, 'https://example.com/feed.xml', title='No Thread')
    db.upsert_episode(slug, ep, title='Ep', status='processing')

    ProcessingQueue().acquire(slug, ep)
    status_service.start_job(slug, ep, 'Ep', 'No Thread')
    # No cancel event registered -> cancel_processing returns False -> fallback release.

    resp = app_client.delete(f'/api/v1/feeds/{slug}')

    assert resp.status_code == 200
    assert ProcessingQueue().get_current() is None
    assert status_service.get_status().current_job is None


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
