"""Tests for queue prioritization: compute_queue_priority and dequeue ordering (#625)."""
from datetime import datetime, timedelta, timezone

from tests.app_bootstrap import bootstrap
bootstrap('queue_priority_test_')

from database.queue import (
    compute_queue_priority,
    FRESH_EPISODE_BOOST,
    MANUAL_REQUEST_BOOST,
    FRESH_WINDOW_HOURS,
)


def _iso(dt):
    return dt.strftime('%Y-%m-%dT%H:%M:%SZ')


def _create_podcast(db, slug):
    """Helper: create a podcast and return its id."""
    db.create_podcast(slug, f'https://example.com/{slug}.xml', slug)
    return db.get_podcast_by_slug(slug)['id']


def _insert_queue_row(db, podcast_id, episode_id, priority=0, minutes_ago=0,
                       status='pending', published_at=None):
    """Helper: insert a queue row with an explicit priority and backdated created_at."""
    conn = db.get_connection()
    conn.execute(
        """INSERT INTO auto_process_queue
           (podcast_id, episode_id, original_url, title, status, priority,
            published_at, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now', ?))""",
        (podcast_id, episode_id, f'https://example.com/{episode_id}.mp3', 'Test',
         status, priority, published_at, f'-{minutes_ago} minutes')
    )
    conn.commit()


class TestComputeQueuePriority:
    def test_base_priority_defaults_to_zero(self):
        assert compute_queue_priority(None, None) == 0

    def test_uses_feed_priority_as_base(self):
        assert compute_queue_priority(10, None) == 10
        assert compute_queue_priority(-10, None) == -10

    def test_manual_boost_applied(self):
        assert compute_queue_priority(0, None, manual=True) == MANUAL_REQUEST_BOOST

    def test_fresh_boost_applied_within_window(self):
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        published = _iso(now - timedelta(hours=1))
        assert compute_queue_priority(0, published, now=now) == FRESH_EPISODE_BOOST

    def test_fresh_boost_not_applied_outside_window(self):
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        published = _iso(now - timedelta(hours=FRESH_WINDOW_HOURS + 1))
        assert compute_queue_priority(0, published, now=now) == 0

    def test_fresh_boost_at_exact_window_boundary(self):
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        published = _iso(now - timedelta(hours=FRESH_WINDOW_HOURS))
        assert compute_queue_priority(0, published, now=now) == FRESH_EPISODE_BOOST

    def test_manual_and_fresh_boosts_stack_on_feed_priority(self):
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        published = _iso(now - timedelta(hours=1))
        assert compute_queue_priority(10, published, manual=True, now=now) == (
            10 + MANUAL_REQUEST_BOOST + FRESH_EPISODE_BOOST
        )

    def test_invalid_published_at_is_ignored(self):
        assert compute_queue_priority(5, 'not-a-date') == 5


class TestQueueOrdering:
    def test_high_priority_beats_older_normal(self, temp_db):
        pid = _create_podcast(temp_db, 'pod-a')
        _insert_queue_row(temp_db, pid, 'old-normal', priority=0, minutes_ago=30)
        _insert_queue_row(temp_db, pid, 'new-high', priority=10, minutes_ago=1)

        assert temp_db.get_next_queued_episode()['episode_id'] == 'new-high'

    def test_low_never_beats_pending_normal(self, temp_db):
        pid = _create_podcast(temp_db, 'pod-b')
        _insert_queue_row(temp_db, pid, 'new-low', priority=-10, minutes_ago=1)
        _insert_queue_row(temp_db, pid, 'old-normal', priority=0, minutes_ago=30)

        assert temp_db.get_next_queued_episode()['episode_id'] == 'old-normal'

    def test_manual_beats_high_feed_priority(self, temp_db):
        pid = _create_podcast(temp_db, 'pod-c')
        _insert_queue_row(temp_db, pid, 'high-feed', priority=10, minutes_ago=1)
        _insert_queue_row(temp_db, pid, 'manual', priority=MANUAL_REQUEST_BOOST, minutes_ago=1)

        assert temp_db.get_next_queued_episode()['episode_id'] == 'manual'

    def test_fifo_within_equal_priority(self, temp_db):
        pid = _create_podcast(temp_db, 'pod-d')
        _insert_queue_row(temp_db, pid, 'second', priority=0, minutes_ago=5)
        _insert_queue_row(temp_db, pid, 'first', priority=0, minutes_ago=10)

        assert temp_db.get_next_queued_episode()['episode_id'] == 'first'

    def test_claim_next_respects_priority_order(self, temp_db):
        pid = _create_podcast(temp_db, 'pod-e')
        _insert_queue_row(temp_db, pid, 'low', priority=-10, minutes_ago=30)
        _insert_queue_row(temp_db, pid, 'high', priority=10, minutes_ago=1)

        claimed = temp_db.claim_next_queued_episode()
        assert claimed['episode_id'] == 'high'
        assert claimed['status'] == 'processing'


class TestPriorityStamping:
    def test_queue_episode_for_processing_stores_priority(self, temp_db):
        temp_db.create_podcast('pod-f', 'https://example.com/pod-f.xml', 'Pod F')
        temp_db.queue_episode_for_processing(
            'pod-f', 'ep1', 'https://example.com/ep1.mp3', priority=10)

        assert temp_db.get_next_queued_episode()['priority'] == 10

    def test_upsert_stores_priority_on_new_row(self, temp_db):
        temp_db.create_podcast('pod-g', 'https://example.com/pod-g.xml', 'Pod G')
        temp_db.upsert_episode_for_processing(
            'pod-g', 'ep1', 'https://example.com/ep1.mp3', priority=25)

        assert temp_db.get_next_queued_episode()['priority'] == 25

    def test_upsert_updates_priority_when_reopening_failed_row(self, temp_db):
        pid = _create_podcast(temp_db, 'pod-h')
        _insert_queue_row(temp_db, pid, 'ep1', priority=0, status='failed')

        temp_db.upsert_episode_for_processing(
            'pod-h', 'ep1', 'https://example.com/ep1.mp3', priority=20)

        assert temp_db.get_next_queued_episode()['priority'] == 20

    def test_upsert_keeps_priority_when_row_already_pending(self, temp_db):
        pid = _create_podcast(temp_db, 'pod-i')
        _insert_queue_row(temp_db, pid, 'ep1', priority=10, status='pending')

        temp_db.upsert_episode_for_processing(
            'pod-i', 'ep1', 'https://example.com/ep1.mp3', priority=0)

        assert temp_db.get_next_queued_episode()['priority'] == 10


class TestRestampPendingPriorities:
    def test_restamp_updates_only_pending_rows(self, temp_db):
        pid = _create_podcast(temp_db, 'pod-j')
        _insert_queue_row(temp_db, pid, 'pending-ep', priority=0, status='pending')
        _insert_queue_row(temp_db, pid, 'processing-ep', priority=0, status='processing')
        _insert_queue_row(temp_db, pid, 'completed-ep', priority=0, status='completed')

        updated = temp_db.restamp_pending_priorities(pid, feed_priority=10)

        assert updated == 1
        conn = temp_db.get_connection()
        rows = {r['episode_id']: r['priority'] for r in conn.execute(
            "SELECT episode_id, priority FROM auto_process_queue WHERE podcast_id = ?", (pid,)
        )}
        assert rows['pending-ep'] == 10
        assert rows['processing-ep'] == 0
        assert rows['completed-ep'] == 0

    def test_restamp_applies_fresh_boost_from_published_at(self, temp_db):
        pid = _create_podcast(temp_db, 'pod-k')
        published = _iso(datetime.now(timezone.utc) - timedelta(hours=1))
        _insert_queue_row(temp_db, pid, 'fresh-ep', priority=0, status='pending',
                          published_at=published)

        temp_db.restamp_pending_priorities(pid, feed_priority=0)

        conn = temp_db.get_connection()
        row = conn.execute(
            "SELECT priority FROM auto_process_queue WHERE episode_id = 'fresh-ep'"
        ).fetchone()
        assert row['priority'] == FRESH_EPISODE_BOOST
