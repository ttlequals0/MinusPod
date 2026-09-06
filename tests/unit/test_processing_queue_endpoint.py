"""GET /episodes/processing surfaces the whole pending queue, not just the head.

The Settings panel previously showed only the active job plus StatusService's
display queue, so an auto-process backlog was invisible. These cover the DB
rows now included, their dequeue ordering, and the dedupe against the active job.
"""
import os
import sys
import tempfile
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
os.environ.setdefault('MINUSPOD_DATA_DIR',
                      tempfile.mkdtemp(prefix='processing-queue-endpoint-test-'))


@pytest.fixture
def seeded_feed(app_client):
    from api import get_database
    db = get_database()
    slug = 'queue-endpoint-feed'
    db.create_podcast(slug, 'https://example.com/feed.xml', 'Queue Endpoint Pod')
    yield {'slug': slug, 'db': db, 'podcast_id': db.get_podcast_by_slug(slug)['id']}
    db.delete_podcast(slug)


def _queue_row(db, podcast_id, episode_id, priority=0, minutes_ago=0, status='pending'):
    conn = db.get_connection()
    conn.execute(
        """INSERT INTO auto_process_queue
           (podcast_id, episode_id, original_url, title, status, priority, created_at)
           VALUES (?, ?, ?, ?, ?, ?, datetime('now', ?))""",
        (podcast_id, episode_id, f'https://example.com/{episode_id}.mp3',
         f'Episode {episode_id}', status, priority, f'-{minutes_ago} minutes')
    )
    conn.commit()


def _authed(client):
    with client.session_transaction() as sess:
        sess['authenticated'] = True
    client.get('/api/v1/auth/status')


def test_returns_all_pending_rows_in_dequeue_order(app_client, seeded_feed):
    db, podcast_id = seeded_feed['db'], seeded_feed['podcast_id']
    _queue_row(db, podcast_id, 'ep-old', priority=0, minutes_ago=30)
    _queue_row(db, podcast_id, 'ep-new', priority=0, minutes_ago=5)
    _queue_row(db, podcast_id, 'ep-urgent', priority=20, minutes_ago=1)
    _authed(app_client)

    resp = app_client.get('/api/v1/episodes/processing')
    assert resp.status_code == 200

    queued = [e for e in resp.get_json() if e['stage'] == 'queued']
    assert [e['episodeId'] for e in queued] == ['ep-urgent', 'ep-old', 'ep-new']
    assert [e['queuePosition'] for e in queued] == [1, 2, 3]
    assert queued[0]['podcast'] == 'Queue Endpoint Pod'
    assert queued[0]['priority'] == 20
    assert queued[0]['queuedAt']
    assert queued[0]['queueTotal'] == 3


def test_row_cap_still_reports_the_uncapped_pending_count(seeded_feed):
    """The endpoint's queueTotal rides on total_pending, counted before LIMIT."""
    db, podcast_id = seeded_feed['db'], seeded_feed['podcast_id']
    for i in range(5):
        _queue_row(db, podcast_id, f'ep-{i}', minutes_ago=10 - i)

    rows = db.get_pending_queued_episodes(limit=2)

    assert len(rows) == 2
    assert rows[0]['total_pending'] == 5


def test_non_pending_rows_are_excluded(app_client, seeded_feed):
    db, podcast_id = seeded_feed['db'], seeded_feed['podcast_id']
    _queue_row(db, podcast_id, 'ep-done', status='completed')
    _queue_row(db, podcast_id, 'ep-failed', status='failed')
    _queue_row(db, podcast_id, 'ep-waiting', status='pending')
    _authed(app_client)

    queued = [e for e in app_client.get('/api/v1/episodes/processing').get_json()
              if e['stage'] == 'queued']
    assert [e['episodeId'] for e in queued] == ['ep-waiting']


def test_active_job_is_not_repeated_in_the_queue(app_client, seeded_feed):
    from api import get_status_service
    db, podcast_id, slug = seeded_feed['db'], seeded_feed['podcast_id'], seeded_feed['slug']
    _queue_row(db, podcast_id, 'ep-live')
    status_service = get_status_service()
    status_service.start_job(slug, 'ep-live', 'Episode ep-live', 'Queue Endpoint Pod')
    _authed(app_client)

    try:
        episodes = app_client.get('/api/v1/episodes/processing').get_json()
    finally:
        status_service.clear_if_matches(slug, 'ep-live')

    matching = [e for e in episodes if e['episodeId'] == 'ep-live']
    assert len(matching) == 1
    assert matching[0]['stage'] != 'queued'


def test_queue_total_is_stamped_on_active_entries_too(app_client, seeded_feed):
    """A page whose queued rows all dedupe away still needs the total, or the
    panel's pager collapses to one page."""
    db, podcast_id = seeded_feed['db'], seeded_feed['podcast_id']
    for i in range(3):
        _queue_row(db, podcast_id, f'ep-{i}')
    db.upsert_episode(seeded_feed['slug'], 'ep-active', title='Active',
                      status='processing',
                      original_url='https://example.com/a.mp3')
    _authed(app_client)

    body = app_client.get('/api/v1/episodes/processing').get_json()
    active = [e for e in body if e['stage'] != 'queued']
    assert active and all(e['queueTotal'] == 3 for e in active)


def test_pagination_returns_offset_aware_positions(app_client, seeded_feed):
    db, podcast_id = seeded_feed['db'], seeded_feed['podcast_id']
    for i in range(5):
        _queue_row(db, podcast_id, f'ep-{i}', minutes_ago=10 - i)
    _authed(app_client)

    page2 = app_client.get('/api/v1/episodes/processing?offset=2&limit=2').get_json()
    queued = [e for e in page2 if e['stage'] == 'queued']
    assert [e['episodeId'] for e in queued] == ['ep-2', 'ep-3']
    assert [e['queuePosition'] for e in queued] == [3, 4]
    assert queued[0]['queueTotal'] == 5


def _csrf(client):
    with client.session_transaction() as sess:
        sess['authenticated'] = True
    client.get('/api/v1/auth/status')
    cookie = client.get_cookie('minuspod_csrf')
    return {'X-CSRF-Token': cookie.value} if cookie else {}


def test_queue_priority_setter_reorders(app_client, seeded_feed):
    db, podcast_id = seeded_feed['db'], seeded_feed['podcast_id']
    _queue_row(db, podcast_id, 'a1b2c3d4e5f6', priority=0, minutes_ago=10)
    _queue_row(db, podcast_id, 'b2c3d4e5f6a1', priority=0, minutes_ago=1)
    _authed(app_client)

    slug = seeded_feed['slug']
    r = app_client.post(f'/api/v1/feeds/{slug}/episodes/b2c3d4e5f6a1/queue-priority',
                        json={'priority': 10}, headers=_csrf(app_client))
    assert r.status_code == 200

    queued = [e for e in app_client.get('/api/v1/episodes/processing').get_json()
              if e['stage'] == 'queued']
    assert [e['episodeId'] for e in queued] == ['b2c3d4e5f6a1', 'a1b2c3d4e5f6']

    # Lowering works too: the direct write is not monotonic like re-enqueue.
    r = app_client.post(f'/api/v1/feeds/{slug}/episodes/b2c3d4e5f6a1/queue-priority',
                        json={'priority': -10}, headers=_csrf(app_client))
    assert r.status_code == 200
    queued = [e for e in app_client.get('/api/v1/episodes/processing').get_json()
              if e['stage'] == 'queued']
    assert [e['episodeId'] for e in queued] == ['a1b2c3d4e5f6', 'b2c3d4e5f6a1']


def test_queue_priority_setter_validation(app_client, seeded_feed):
    slug = seeded_feed['slug']
    _authed(app_client)
    hdr = _csrf(app_client)
    missing = 'c3d4e5f6a1b2'

    assert app_client.post(f'/api/v1/feeds/{slug}/episodes/{missing}/queue-priority',
                           json={'priority': 'high'}, headers=hdr).status_code == 400
    assert app_client.post(f'/api/v1/feeds/{slug}/episodes/{missing}/queue-priority',
                           json={'priority': True}, headers=hdr).status_code == 400
    assert app_client.post(f'/api/v1/feeds/{slug}/episodes/{missing}/queue-priority',
                           json={}, headers=hdr).status_code == 400
    # No pending row for that episode.
    assert app_client.post(f'/api/v1/feeds/{slug}/episodes/{missing}/queue-priority',
                           json={'priority': 5}, headers=hdr).status_code == 404
    assert app_client.post(f'/api/v1/feeds/no-such-feed/episodes/{missing}/queue-priority',
                           json={'priority': 5}, headers=hdr).status_code == 404
    # Exactly one of priority/delta, and both stay inside the clamp range.
    assert app_client.post(f'/api/v1/feeds/{slug}/episodes/{missing}/queue-priority',
                           json={'priority': 5, 'delta': 1}, headers=hdr).status_code == 400
    assert app_client.post(f'/api/v1/feeds/{slug}/episodes/{missing}/queue-priority',
                           json={'priority': 10 ** 9}, headers=hdr).status_code == 400


def test_queue_priority_delta_is_applied_server_side(app_client, seeded_feed):
    """A stepper sends a delta so a click made against a stale list value is
    still added to whatever the row currently holds."""
    db, podcast_id = seeded_feed['db'], seeded_feed['podcast_id']
    _queue_row(db, podcast_id, 'd4e5f6a1b2c3', priority=0)
    _authed(app_client)
    slug, hdr = seeded_feed['slug'], _csrf(app_client)
    url = f'/api/v1/feeds/{slug}/episodes/d4e5f6a1b2c3/queue-priority'

    assert app_client.post(url, json={'delta': 5}, headers=hdr).get_json()['priority'] == 5
    assert app_client.post(url, json={'delta': 5}, headers=hdr).get_json()['priority'] == 10
    assert app_client.post(url, json={'delta': -25}, headers=hdr).get_json()['priority'] == -15


def test_queue_priority_clamps_to_bounds(app_client, seeded_feed):
    from database.queue import QUEUE_PRIORITY_MAX
    db, podcast_id = seeded_feed['db'], seeded_feed['podcast_id']
    _queue_row(db, podcast_id, 'e5f6a1b2c3d4', priority=QUEUE_PRIORITY_MAX)
    _authed(app_client)
    slug, hdr = seeded_feed['slug'], _csrf(app_client)
    r = app_client.post(f'/api/v1/feeds/{slug}/episodes/e5f6a1b2c3d4/queue-priority',
                        json={'delta': 100}, headers=hdr)
    assert r.get_json()['priority'] == QUEUE_PRIORITY_MAX


def test_display_queue_extra_is_not_listed_twice(app_client, seeded_feed):
    """A display-queue entry repeated by StatusService must not render twice
    or inflate queueTotal."""
    from api import get_status_service
    _authed(app_client)
    svc = get_status_service()
    entry = {'slug': seeded_feed['slug'], 'episode_id': 'f6a1b2c3d4e5',
             'title': 'Extra', 'podcast_name': 'Queue Endpoint Pod', 'queued_at': 0}
    snapshot = svc.get_status()
    snapshot.queued_episodes = [entry, dict(entry)]
    with patch.object(svc, 'get_status', return_value=snapshot):
        queued = [e for e in app_client.get('/api/v1/episodes/processing').get_json()
                  if e['stage'] == 'queued']
    assert [e['episodeId'] for e in queued] == ['f6a1b2c3d4e5']
    assert queued[0]['queueTotal'] == 1


def test_set_queue_row_priority_ignores_non_pending_rows(seeded_feed):
    db, podcast_id = seeded_feed['db'], seeded_feed['podcast_id']
    _queue_row(db, podcast_id, 'ep-done', status='completed')
    slug = seeded_feed['slug']
    assert db.set_queue_row_priority(slug, 'ep-done', priority=10) is None
    assert db.set_queue_row_priority(slug, 'missing', priority=10) is None
