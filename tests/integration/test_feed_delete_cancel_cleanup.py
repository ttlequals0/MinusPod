"""Integration tests for feed-delete / cancel job cleanup (#525).

Deletion requests leave rows and files intact until the durable run owner has
stopped. A retry can complete deletion after cancellation is acknowledged.
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


def test_delete_feed_waits_for_active_owner(app_client):
    from api import get_database, get_status_service
    from processing_queue import ProcessingQueue

    db = get_database()
    status_service = get_status_service()
    slug, ep = 'active-job-feed', 'ep-active'
    db.create_podcast(slug, 'https://example.com/feed.xml', title='Active Job')
    db.upsert_episode(slug, ep, title='Ep', status='processing')

    run_id = ProcessingQueue().acquire(slug, ep)
    status_service.start_job(slug, ep, 'Ep', 'Active Job')
    event = _register_event(run_id)

    resp = app_client.delete(f'/api/v1/feeds/{slug}')

    assert resp.status_code == 202
    assert event.is_set()
    assert status_service.get_status().current_job is not None
    assert ProcessingQueue().get_current() == [(slug, ep)]
    assert db.get_podcast_by_slug(slug) is not None


def test_delete_feed_never_releases_another_worker(app_client):
    from api import get_database, get_status_service
    from processing_queue import ProcessingQueue

    db = get_database()
    status_service = get_status_service()
    slug, ep = 'no-thread-feed', 'ep-orphan'
    db.create_podcast(slug, 'https://example.com/feed.xml', title='No Thread')
    db.upsert_episode(slug, ep, title='Ep', status='processing')

    ProcessingQueue().acquire(slug, ep)
    status_service.start_job(slug, ep, 'Ep', 'No Thread')
    resp = app_client.delete(f'/api/v1/feeds/{slug}')

    assert resp.status_code == 202
    assert ProcessingQueue().get_current() == [(slug, ep)]
    assert status_service.get_status().current_job is not None
    assert db.get_podcast_by_slug(slug) is not None


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
