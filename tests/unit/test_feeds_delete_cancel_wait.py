"""DELETE /feeds/<slug> cancellation handshake.

Deleting a podcast flags its in-flight run, waits a bounded time for the
owner to acknowledge, and then deletes regardless: an owner that never
checks in must not leave a feed undeletable. A cancellation that cannot be
recorded is a 503 instead, so the status bar never advertises a feed that
is about to disappear.
"""
import os
import sys
import tempfile
import threading
import time
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='feeds-delete-cancel-wait-'))


def _authed(client):
    with client.session_transaction() as sess:
        sess['authenticated'] = True
    client.get('/api/v1/auth/status')


def _csrf_headers(client):
    csrf = None
    for cookie in client._cookies.values():
        if cookie.key == 'minuspod_csrf':
            csrf = cookie.value
    return {'X-CSRF-Token': csrf} if csrf else {}


@pytest.fixture(autouse=True)
def _clean_processing_state():
    """Release any run this module's tests left claimed."""
    from api import get_status_service
    from processing_queue import ProcessingQueue
    from cancel import _cancel_events, _cancel_events_lock

    def _reset():
        queue = ProcessingQueue()
        for slug, episode_id in queue.get_current():
            run_id = queue.active_run_id(slug, episode_id)
            if run_id and run_id != '__database_unavailable__':
                queue.release(run_id, terminal_state='interrupted')
            get_status_service().clear_if_matches(slug, episode_id)
        with _cancel_events_lock:
            _cancel_events.clear()

    _reset()
    yield
    _reset()


def _start_run(db, queue, status_service, slug, episode_id):
    db.create_podcast(slug, 'https://example.com/feed.xml', title='Show')
    db.upsert_episode(slug, episode_id, title='Ep', status='processing')
    run_id = queue.acquire(slug, episode_id)
    status_service.start_job(slug, episode_id, 'Ep', 'Show', run_id=run_id)
    return run_id


def test_delete_waits_for_an_acknowledging_run(app_client):
    """The owner's terminal-state release lands before the response."""
    from api import get_database, get_status_service
    from cancel import _cancel_events, _cancel_events_lock
    from processing_queue import ProcessingQueue

    db, status_service, queue = get_database(), get_status_service(), ProcessingQueue()
    slug, episode_id = 'delete-wait-ack', 'ep-ack'
    run_id = _start_run(db, queue, status_service, slug, episode_id)
    event = threading.Event()
    with _cancel_events_lock:
        _cancel_events[run_id] = event
    order = []

    def _owner():
        if event.wait(timeout=5.0):
            time.sleep(0.15)
            order.append('acknowledged')
            queue.release(run_id, terminal_state='interrupted')

    owner = threading.Thread(target=_owner)
    owner.start()
    _authed(app_client)
    try:
        resp = app_client.delete(f'/api/v1/feeds/{slug}', headers=_csrf_headers(app_client))
        order.append('deleted')
    finally:
        owner.join(timeout=5.0)

    assert resp.status_code == 200
    assert order == ['acknowledged', 'deleted']
    assert db.get_podcast_by_slug(slug) is None
    assert status_service.get_status().current_job is None


def test_delete_completes_when_the_run_never_acknowledges(app_client, caplog):
    """A wedged owner must not make the feed undeletable."""
    from api import get_database, get_status_service
    from processing_queue import ProcessingQueue

    db, status_service, queue = get_database(), get_status_service(), ProcessingQueue()
    slug, episode_id = 'delete-wait-wedged', 'ep-wedged'
    _start_run(db, queue, status_service, slug, episode_id)
    _authed(app_client)

    with patch('api.feeds._DELETE_CANCEL_WAIT_SECONDS', 0.2), caplog.at_level('WARNING'):
        resp = app_client.delete(f'/api/v1/feeds/{slug}', headers=_csrf_headers(app_client))

    assert resp.status_code == 200
    assert db.get_podcast_by_slug(slug) is None
    assert queue.get_current() == []
    assert status_service.get_status().current_job is None
    assert 'did not acknowledge' in caplog.text


def test_unrecorded_cancellation_returns_503_and_keeps_the_feed(app_client):
    """A cancellation that cannot be recorded aborts the delete."""
    from api import get_database, get_status_service
    from processing_queue import ProcessingQueue

    db, status_service, queue = get_database(), get_status_service(), ProcessingQueue()
    slug, episode_id = 'delete-cancel-unrecorded', 'ep-unrecorded'
    _start_run(db, queue, status_service, slug, episode_id)
    _authed(app_client)

    with patch('api.feeds.request_cancellation', return_value=None):
        resp = app_client.delete(f'/api/v1/feeds/{slug}', headers=_csrf_headers(app_client))

    assert resp.status_code == 503
    assert db.get_podcast_by_slug(slug) is not None
    assert queue.active_run_id(slug, episode_id) is not None


def test_delete_proceeds_when_the_run_ended_before_the_cancel(app_client):
    """No run left to cancel is not a failure: the delete continues."""
    from api import get_database, get_status_service
    from processing_queue import ProcessingQueue

    db, status_service, queue = get_database(), get_status_service(), ProcessingQueue()
    slug, episode_id = 'delete-cancel-raced', 'ep-raced'
    run_id = _start_run(db, queue, status_service, slug, episode_id)

    def _finished_before_cancel(_slug, _episode_id):
        queue.release(run_id, terminal_state='interrupted')
        return None

    _authed(app_client)
    with patch('api.feeds.request_cancellation', _finished_before_cancel):
        resp = app_client.delete(f'/api/v1/feeds/{slug}', headers=_csrf_headers(app_client))

    assert resp.status_code == 200
    assert db.get_podcast_by_slug(slug) is None
    # The owner clears its own display entry; this run was never real.
    status_service.clear_if_matches(slug, episode_id, run_id=run_id)


def test_aborted_delete_unfences_the_feed(app_client):
    """The delete marker blocks acquisition, so a 503 must not leave it set."""
    from api import get_database, get_status_service
    from processing_queue import ProcessingQueue

    db, status_service, queue = get_database(), get_status_service(), ProcessingQueue()
    slug, episode_id = 'delete-cancel-unfence', 'ep-unfence'
    run_id = _start_run(db, queue, status_service, slug, episode_id)
    _authed(app_client)

    with patch('api.feeds.request_cancellation', return_value=None):
        resp = app_client.delete(f'/api/v1/feeds/{slug}', headers=_csrf_headers(app_client))

    assert resp.status_code == 503
    assert db.get_podcast_by_slug(slug)['deletion_requested_at'] is None
    queue.release(run_id, terminal_state='interrupted')
    status_service.clear_if_matches(slug, episode_id, run_id=run_id)
    db.delete_podcast(slug)
