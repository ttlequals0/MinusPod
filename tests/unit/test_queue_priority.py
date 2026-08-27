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

    def test_fresh_boost_applied_by_default(self):
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        published = _iso(now - timedelta(hours=1))
        assert compute_queue_priority(0, published, now=now) == FRESH_EPISODE_BOOST

    def test_fresh_boost_absent_when_disabled(self):
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        published = _iso(now - timedelta(hours=1))
        assert compute_queue_priority(0, published, now=now, apply_fresh_boost=False) == 0


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

    def test_upsert_raises_priority_when_row_already_pending(self, temp_db):
        # The guard exists so a background re-upsert cannot stomp a boost.
        # It must not work in reverse: an auto-queued episode (fresh boost
        # only) that the user then plays or reprocesses has to climb, or a
        # bulk backlog pins it for days (PSW #941, 2026-08-27: JIT boost
        # to 25 was discarded and the episode sat 94th behind 93 rows).
        pid = _create_podcast(temp_db, 'pod-raise')
        _insert_queue_row(temp_db, pid, 'ep1', priority=5, status='pending')

        temp_db.upsert_episode_for_processing(
            'pod-raise', 'ep1', 'https://example.com/ep1.mp3', priority=25)

        assert temp_db.get_next_queued_episode()['priority'] == 25


class TestBulkEnqueuePriority:
    def test_bulk_reprocess_does_not_get_the_manual_boost(self, temp_db):
        # Reprocess All on an old backlog must not outrank a JIT play or a
        # single manual reprocess of a current episode.
        two_years_ago = _iso(datetime.now(timezone.utc) - timedelta(days=730))
        bulk = compute_queue_priority(0, two_years_ago, manual=False)
        jit = compute_queue_priority(0, None, manual=True)
        assert bulk == 0
        assert jit > bulk

    def test_fresh_auto_episode_outranks_bulk_backlog(self, temp_db):
        fresh = _iso(datetime.now(timezone.utc) - timedelta(hours=1))
        two_years_ago = _iso(datetime.now(timezone.utc) - timedelta(days=730))
        assert compute_queue_priority(0, fresh) > compute_queue_priority(
            0, two_years_ago, manual=False)


class TestConfigurableBoosts:
    def test_defaults_match_the_constants(self, temp_db):
        assert compute_queue_priority(0, None, manual=True) == MANUAL_REQUEST_BOOST
        fresh = _iso(datetime.now(timezone.utc) - timedelta(hours=1))
        assert compute_queue_priority(0, fresh) == FRESH_EPISODE_BOOST

    def test_configured_boosts_are_read_from_settings(self, temp_db):
        temp_db.set_setting('queue_manual_boost', '50', is_default=False)
        temp_db.set_setting('queue_fresh_boost', '2', is_default=False)
        temp_db.set_setting('queue_bulk_boost', '7', is_default=False)
        fresh = _iso(datetime.now(timezone.utc) - timedelta(hours=1))
        assert compute_queue_priority(0, None, manual=True) == 50
        assert compute_queue_priority(0, fresh) == 2
        assert compute_queue_priority(0, None, bulk=True) == 7

    def test_malformed_setting_falls_back_to_the_constant(self, temp_db):
        temp_db.set_setting('queue_manual_boost', 'lots', is_default=False)
        assert compute_queue_priority(0, None, manual=True) == MANUAL_REQUEST_BOOST


class TestJitClimbsPastBulkBacklog:
    def test_played_episode_dequeues_before_bulk_rows(self, temp_db):
        # The 2026-08-27 incident end to end: bulk backlog at 20, episode
        # auto-queued at 5, then a play request re-upserts at 25. It must
        # be the next dequeue, not 94th.
        pid = _create_podcast(temp_db, 'pod-incident')
        for i in range(3):
            _insert_queue_row(temp_db, pid, f'bulk-{i}', priority=20,
                              minutes_ago=120)
        _insert_queue_row(temp_db, pid, 'played-ep', priority=5, minutes_ago=10)

        temp_db.upsert_episode_for_processing(
            'pod-incident', 'played-ep', 'https://example.com/p.mp3',
            priority=25)

        assert temp_db.get_next_queued_episode()['episode_id'] == 'played-ep'


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

    def test_restamp_skips_fresh_boost_when_setting_off(self, temp_db):
        pid = _create_podcast(temp_db, 'pod-l')
        temp_db.set_setting('process_new_episodes_first', 'false', is_default=False)
        published = _iso(datetime.now(timezone.utc) - timedelta(hours=1))
        _insert_queue_row(temp_db, pid, 'fresh-ep', priority=0, status='pending',
                          published_at=published)

        temp_db.restamp_pending_priorities(pid, feed_priority=0)

        conn = temp_db.get_connection()
        row = conn.execute(
            "SELECT priority FROM auto_process_queue WHERE episode_id = 'fresh-ep'"
        ).fetchone()
        assert row['priority'] == 0

    def test_restamp_applies_fresh_boost_when_setting_on_by_default(self, temp_db):
        pid = _create_podcast(temp_db, 'pod-m')
        published = _iso(datetime.now(timezone.utc) - timedelta(hours=1))
        _insert_queue_row(temp_db, pid, 'fresh-ep', priority=0, status='pending',
                          published_at=published)

        temp_db.restamp_pending_priorities(pid, feed_priority=0)

        conn = temp_db.get_connection()
        row = conn.execute(
            "SELECT priority FROM auto_process_queue WHERE episode_id = 'fresh-ep'"
        ).fetchone()
        assert row['priority'] == FRESH_EPISODE_BOOST
