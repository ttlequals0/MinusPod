"""GET /episodes/processing surfaces the whole pending queue, not just the head.

The Settings panel previously showed only the active job plus StatusService's
display queue, so an auto-process backlog was invisible. These cover the DB
rows now included, their dequeue ordering, and the dedupe against the active job.
"""
import os
import sys
import tempfile

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
