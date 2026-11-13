"""Processing order of queued episodes: places stay, the feed's order picks who takes them."""
import random
import threading
import time

import pytest

from tests.queue_helpers import peek_next_queued
from database.queue import DEFAULT_QUEUE_ORDER, QUEUE_ORDERS, resolve_queue_order


def _feed(db, slug, order=None):
    db.create_podcast(slug, f'https://example.com/{slug}.xml', slug)
    if order is not None:
        db.get_connection().execute("UPDATE podcasts SET queue_order = ? WHERE slug = ?", (order, slug))
        db.get_connection().commit()
    return db.get_podcast_by_slug(slug)['id']


def _row(db, pid, name, published=None, priority=0, created='2026-01-01T00:00:00Z', status='pending'):
    conn = db.get_connection()
    conn.execute(
        """INSERT INTO auto_process_queue
           (podcast_id, episode_id, original_url, title, status, priority, published_at, created_at, updated_at)
           VALUES (?, ?, ?, 'T', ?, ?, ?, ?, ?)""",
        (pid, name, f'https://example.com/{name}.mp3', status, priority, published, created, created))
    conn.commit()


def _order(db):
    return [r['episode_id'] for r in db.get_pending_queued_episodes(limit=10000)]


def _fill(db, pid, dates, prefix='e'):
    """Rows queued in the given order (distinct seconds) with the given publish dates."""
    for i, published in enumerate(dates):
        _row(db, pid, f'{prefix}{i}', published, created=f'2026-01-01T00:00:{i:02d}Z')


DATES = ['2026-03-01T00:00:00Z', '2026-01-01T00:00:00Z', '2026-02-01T00:00:00Z']


class TestResolve:
    def test_values_and_default(self):
        assert QUEUE_ORDERS == ('queued', 'oldest_first', 'newest_first')
        assert DEFAULT_QUEUE_ORDER == 'queued'

    @pytest.mark.parametrize('feed,glob,expected', [
        (None, None, 'queued'), (None, 'oldest_first', 'oldest_first'),
        ('newest_first', 'oldest_first', 'newest_first'), ('bogus', 'oldest_first', 'oldest_first'),
        (None, 'bogus', 'queued'), ('', 'newest_first', 'newest_first'),
    ])
    def test_resolve(self, feed, glob, expected):
        assert resolve_queue_order(feed, glob) == expected


class TestSingleFeed:
    def test_as_queued_is_insertion_order(self, temp_db):
        pid = _feed(temp_db, 'a')
        _fill(temp_db, pid, DATES)
        assert _order(temp_db) == ['e0', 'e1', 'e2']

    def test_oldest_first(self, temp_db):
        pid = _feed(temp_db, 'a', 'oldest_first')
        _fill(temp_db, pid, DATES)
        assert _order(temp_db) == ['e1', 'e2', 'e0']

    def test_newest_first(self, temp_db):
        pid = _feed(temp_db, 'a', 'newest_first')
        _fill(temp_db, pid, DATES)
        assert _order(temp_db) == ['e0', 'e2', 'e1']

    def test_global_setting_applies_without_a_feed_value(self, temp_db):
        pid = _feed(temp_db, 'a')
        _fill(temp_db, pid, DATES)
        temp_db.set_setting('queue_order', 'oldest_first')
        assert _order(temp_db) == ['e1', 'e2', 'e0']

    def test_feed_value_beats_global(self, temp_db):
        pid = _feed(temp_db, 'a', 'newest_first')
        _fill(temp_db, pid, DATES)
        temp_db.set_setting('queue_order', 'oldest_first')
        assert _order(temp_db) == ['e0', 'e2', 'e1']

    def test_unknown_stored_feed_value_falls_back_to_global(self, temp_db):
        pid = _feed(temp_db, 'a', 'sideways')
        _fill(temp_db, pid, DATES)
        temp_db.set_setting('queue_order', 'oldest_first')
        assert _order(temp_db) == ['e1', 'e2', 'e0']

    def test_changing_the_setting_resorts_without_writing_the_queue(self, temp_db):
        pid = _feed(temp_db, 'a')
        _fill(temp_db, pid, DATES)
        before = temp_db.get_connection().execute(
            "SELECT id, updated_at, created_at, priority FROM auto_process_queue ORDER BY id").fetchall()
        assert _order(temp_db) == ['e0', 'e1', 'e2']
        temp_db.set_setting('queue_order', 'newest_first')
        assert _order(temp_db) == ['e0', 'e2', 'e1']
        temp_db.set_setting('queue_order', 'oldest_first')
        assert _order(temp_db) == ['e1', 'e2', 'e0']
        after = temp_db.get_connection().execute(
            "SELECT id, updated_at, created_at, priority FROM auto_process_queue ORDER BY id").fetchall()
        assert [tuple(r) for r in before] == [tuple(r) for r in after]


class TestPriority:
    def test_higher_priority_runs_first_in_both_orders(self, temp_db):
        pid = _feed(temp_db, 'a', 'oldest_first')
        _fill(temp_db, pid, DATES)
        _row(temp_db, pid, 'boosted', '2027-01-01T00:00:00Z', priority=5, created='2026-01-01T00:01:00Z')
        _row(temp_db, pid, 'manual', '2027-02-01T00:00:00Z', priority=20, created='2026-01-01T00:02:00Z')
        _row(temp_db, pid, 'low', '2000-01-01T00:00:00Z', priority=-10, created='2026-01-01T00:03:00Z')
        assert _order(temp_db) == ['manual', 'boosted', 'e1', 'e2', 'e0', 'low']

    def test_order_applies_only_within_one_priority(self, temp_db):
        pid = _feed(temp_db, 'a', 'newest_first')
        _row(temp_db, pid, 'old', '2026-01-01T00:00:00Z', priority=3, created='2026-01-01T00:00:01Z')
        _row(temp_db, pid, 'new', '2026-06-01T00:00:00Z', priority=3, created='2026-01-01T00:00:02Z')
        _row(temp_db, pid, 'plain', '2026-12-01T00:00:00Z', priority=0, created='2026-01-01T00:00:00Z')
        assert _order(temp_db) == ['new', 'old', 'plain']


class TestInterleavedFeeds:
    def test_other_feed_keeps_its_exact_places(self, temp_db):
        a = _feed(temp_db, 'a', 'oldest_first')
        b = _feed(temp_db, 'b')
        stamps = iter(range(60))

        def add(pid, name, published):
            _row(temp_db, pid, name, published, created=f'2026-01-01T00:00:{next(stamps):02d}Z')
        add(a, 'a0', '2026-03-01T00:00:00Z')
        add(b, 'b0', '2026-03-01T00:00:00Z')
        add(a, 'a1', '2026-01-01T00:00:00Z')
        add(b, 'b1', '2026-01-01T00:00:00Z')
        add(a, 'a2', '2026-02-01T00:00:00Z')
        assert _order(temp_db) == ['a1', 'b0', 'a2', 'b1', 'a0']


class TestDates:
    def test_undated_rows_go_last_in_both_date_orders(self, temp_db):
        pid = _feed(temp_db, 'a', 'oldest_first')
        _fill(temp_db, pid, [None, '2026-02-01T00:00:00Z', '', '2026-01-01T00:00:00Z'])
        assert _order(temp_db) == ['e3', 'e1', 'e0', 'e2']
        temp_db.set_setting('queue_order', 'x')
        temp_db.get_connection().execute("UPDATE podcasts SET queue_order = 'newest_first'")
        temp_db.get_connection().commit()
        assert _order(temp_db) == ['e1', 'e3', 'e0', 'e2']

    def test_equal_dates_fall_back_to_time_queued(self, temp_db):
        pid = _feed(temp_db, 'a', 'newest_first')
        _fill(temp_db, pid, ['2026-01-01T00:00:00Z'] * 3)
        assert _order(temp_db) == ['e0', 'e1', 'e2']

    def test_same_second_rows_fall_back_to_id(self, temp_db):
        pid = _feed(temp_db, 'a', 'oldest_first')
        for i in range(3):
            _row(temp_db, pid, f'e{i}', '2026-01-01T00:00:00Z')
        assert _order(temp_db) == ['e0', 'e1', 'e2']

    def test_mixed_iso_formats_compare_by_instant(self, temp_db):
        pid = _feed(temp_db, 'a', 'oldest_first')
        _fill(temp_db, pid, [
            '2026-01-01T12:00:00+05:00',        # 07:00Z
            '2026-01-01T09:00:00Z',
            '2026-01-01T08:00:00.500000Z',
            '2026-01-01T03:00:00-03:00',        # 06:00Z
            '2026-01-01T08:30:00',              # naive: UTC
        ])
        assert _order(temp_db) == ['e3', 'e0', 'e2', 'e4', 'e1']

    def test_rfc_2822_leftovers_sort_like_undated_until_normalized(self, temp_db):
        pid = _feed(temp_db, 'a', 'oldest_first')
        _fill(temp_db, pid, ['Tue, 10 Mar 2026 19:10:06 GMT', '2026-01-01T00:00:00Z'])
        assert _order(temp_db) == ['e1', 'e0']


class TestWritesAndMigration:
    def test_every_queue_write_stores_iso_dates(self, temp_db):
        _feed(temp_db, 'a')
        rfc = 'Tue, 10 Mar 2026 19:10:06 GMT'
        temp_db.queue_episode_for_processing('a', 'one', 'https://example.com/1.mp3', published_at=rfc)
        temp_db.queue_episodes_for_processing('a', [{'episode_id': 'two', 'original_url': 'u', 'published_at': rfc}])
        temp_db.upsert_episode_for_processing('a', 'three', 'u', published_at=rfc)
        rows = temp_db.get_connection().execute("SELECT published_at FROM auto_process_queue").fetchall()
        assert [r[0] for r in rows] == ['2026-03-10T19:10:06Z'] * 3

    def test_migration_normalizes_old_rows_and_keeps_the_rest(self, temp_db):
        pid = _feed(temp_db, 'a')
        _fill(temp_db, pid, ['Tue, 10 Mar 2026 19:10:06 GMT', '2026-01-01T00:00:00+02:00', None, 'not a date',
                             '10 Mar 2026 19:10:06 GMT'])
        self._rerun_migration(temp_db)
        assert self._stored(temp_db) == ['2026-03-10T19:10:06Z', '2025-12-31T22:00:00Z', None, 'not a date',
                                         '2026-03-10T19:10:06Z']

    def test_migration_runs_once(self, temp_db):
        pid = _feed(temp_db, 'a')
        self._rerun_migration(temp_db)
        _fill(temp_db, pid, ['Tue, 10 Mar 2026 19:10:06 GMT'])
        temp_db._run_schema_migrations()
        assert self._stored(temp_db) == ['Tue, 10 Mar 2026 19:10:06 GMT']

    @staticmethod
    def _rerun_migration(db):
        db.get_connection().execute(
            "DELETE FROM schema_migrations WHERE name = 'normalize_queue_published_at_once'")
        db.get_connection().commit()
        db._run_schema_migrations()

    @staticmethod
    def _stored(db):
        return [r[0] for r in db.get_connection().execute("SELECT published_at FROM auto_process_queue ORDER BY id")]

    def test_stranded_requeue_stores_iso_dates(self, temp_db):
        _feed(temp_db, 'a')
        temp_db.upsert_episode('a', 'ep1', original_url='u', title='T', status='pending',
                               published_at='10 Mar 2026 19:10:06 GMT')
        temp_db.requeue_stranded_pending_episodes('a', [{'episode_id': 'ep1', 'priority': 0}], '2026-01-01T00:00:00Z')
        assert self._stored(temp_db) == ['2026-03-10T19:10:06Z']

    def test_held_episode_migration_stores_iso_dates(self, temp_db):
        _feed(temp_db, 'a')
        temp_db.upsert_episode('a', 'ep1', original_url='u', title='T', status='deferred',
                               published_at='10 Mar 2026 19:10:06 GMT')
        temp_db.get_connection().execute(
            "UPDATE episodes SET deferred_service = 'llm_rate_limit' WHERE episode_id = 'ep1'")
        temp_db.get_connection().execute(
            "DELETE FROM schema_migrations WHERE name = 'requeue_rate_limit_held_episodes'")
        temp_db.get_connection().commit()
        temp_db._run_requeue_rate_limit_held_episodes(temp_db.get_connection())
        assert self._stored(temp_db) == ['2026-03-10T19:10:06Z']


class TestUpgrade:
    def test_database_without_the_column_keeps_todays_order(self, temp_db):
        pid = _feed(temp_db, 'a')
        _fill(temp_db, pid, DATES)
        conn = temp_db.get_connection()
        conn.execute("ALTER TABLE podcasts DROP COLUMN queue_order")
        conn.commit()
        temp_db._run_schema_migrations()
        assert 'queue_order' in {r[1] for r in conn.execute("PRAGMA table_info(podcasts)")}
        assert conn.execute("SELECT queue_order FROM podcasts").fetchone()[0] is None
        assert _order(temp_db) == ['e0', 'e1', 'e2']


class TestClaim:
    def test_claim_takes_the_first_listed_row(self, temp_db):
        pid = _feed(temp_db, 'a', 'newest_first')
        _fill(temp_db, pid, DATES)
        assert peek_next_queued(temp_db)['episode_id'] == _order(temp_db)[0]
        claimed = [temp_db.claim_next_queued_episode()['episode_id'] for _ in range(3)]
        assert claimed == ['e0', 'e2', 'e1']
        assert temp_db.claim_next_queued_episode() is None

    def test_excluded_rows_hold_no_place(self, temp_db):
        pid = _feed(temp_db, 'a', 'oldest_first')
        _fill(temp_db, pid, DATES)
        # e1 (oldest) is excluded: the remaining rows reorder among the places they occupy.
        claimed = temp_db.claim_next_queued_episode(exclude_episodes=[('a', 'e1')])
        assert claimed['episode_id'] == 'e2'
        assert claimed['status'] == 'processing'
        assert temp_db.claim_next_queued_episode(exclude_episodes=[('a', 'e1')])['episode_id'] == 'e0'
        assert temp_db.claim_next_queued_episode(exclude_episodes=[('a', 'e1')]) is None
        assert temp_db.claim_next_queued_episode()['episode_id'] == 'e1'

    def test_exclusion_inside_another_feed_does_not_shift_places(self, temp_db):
        a = _feed(temp_db, 'a', 'oldest_first')
        b = _feed(temp_db, 'b')
        _row(temp_db, a, 'a0', '2026-03-01T00:00:00Z', created='2026-01-01T00:00:00Z')
        _row(temp_db, b, 'b0', None, created='2026-01-01T00:00:01Z')
        _row(temp_db, a, 'a1', '2026-01-01T00:00:00Z', created='2026-01-01T00:00:02Z')
        assert temp_db.claim_next_queued_episode(exclude_episodes=[('b', 'b0')])['episode_id'] == 'a1'

    def test_two_connections_never_claim_the_same_row(self, temp_db):
        pid = _feed(temp_db, 'a', 'oldest_first')
        _fill(temp_db, pid, [f'2026-01-{d:02d}T00:00:00Z' for d in range(1, 29)])
        claimed, lock = [], threading.Lock()

        def worker():
            while (row := temp_db.claim_next_queued_episode()):
                with lock:
                    claimed.append(row['episode_id'])
        threads = [threading.Thread(target=worker) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        assert len(claimed) == 28 and len(set(claimed)) == 28


class TestListAndStatus:
    def test_pages_join_into_the_unpaged_order(self, temp_db):
        a = _feed(temp_db, 'a', 'newest_first')
        b = _feed(temp_db, 'b')
        for i in range(9):
            _row(temp_db, a, f'a{i}', f'2026-01-{i + 1:02d}T00:00:00Z', created=f'2026-01-01T00:00:{2 * i:02d}Z')
            _row(temp_db, b, f'b{i}', f'2026-01-{i + 1:02d}T00:00:00Z', created=f'2026-01-01T00:00:{2 * i + 1:02d}Z')
        whole = temp_db.get_pending_queued_episodes()
        pages = []
        for offset in range(0, 18, 5):
            page = temp_db.get_pending_queued_episodes(limit=5, offset=offset)
            assert {r['total_pending'] for r in page} == {18}
            pages += page
        assert [(r['podcast_slug'], r['episode_id']) for r in pages] == \
               [(r['podcast_slug'], r['episode_id']) for r in whole]

    def test_status_items_match_the_list_and_keep_processing_rows(self, temp_db):
        pid = _feed(temp_db, 'a', 'oldest_first')
        _fill(temp_db, pid, DATES)
        _row(temp_db, pid, 'busy', None, priority=50, created='2026-01-01T00:05:00Z', status='processing')
        status = temp_db.get_queue_status()
        assert [i['episode_id'] for i in status['items']] == ['busy', 'e1', 'e2', 'e0']
        assert status['pending'] == 3 and status['processing'] == 1
        assert [i['episode_id'] for i in status['items'] if i['status'] == 'pending'] == _order(temp_db)


class TestLegacyEquivalence:
    def test_all_queued_equals_the_legacy_order_on_500_rows(self, temp_db):
        rng = random.Random(997)
        pids = [_feed(temp_db, f'f{i}') for i in range(6)]
        conn = temp_db.get_connection()
        for i in range(500):
            second = rng.randrange(0, 40)
            _row(temp_db, rng.choice(pids), f'e{i}',
                 rng.choice([None, f'2026-0{rng.randint(1, 9)}-01T00:00:00Z']),
                 priority=rng.choice([-10, 0, 0, 5, 20]), created=f'2026-01-01T00:00:{second:02d}Z',
                 status=rng.choice(['pending'] * 4 + ['completed']))
        legacy = [r[0] for r in conn.execute(
            """SELECT q.episode_id FROM auto_process_queue q JOIN podcasts p ON q.podcast_id = p.id
               WHERE q.status = 'pending' ORDER BY q.priority DESC, q.created_at ASC, q.id ASC""")]
        assert _order(temp_db) == legacy
        sqlite_legacy = [(r[0], r[1]) for r in conn.execute(
            """SELECT q.priority, q.created_at FROM auto_process_queue q JOIN podcasts p ON q.podcast_id = p.id
               WHERE q.status = 'pending' ORDER BY q.priority DESC, q.created_at ASC""")]
        listed = {(r['episode_id']): (r['priority'], r['created_at']) for r in temp_db.get_pending_queued_episodes(limit=10000)}
        assert [listed[e] for e in _order(temp_db)] == sqlite_legacy


def test_five_thousand_row_claim_timing(temp_db, capsys):
    rng = random.Random(5000)
    pids = [_feed(temp_db, f'f{i}', rng.choice(QUEUE_ORDERS)) for i in range(20)]
    conn = temp_db.get_connection()
    conn.executemany(
        """INSERT INTO auto_process_queue (podcast_id, episode_id, original_url, priority, published_at, created_at)
           VALUES (?, ?, 'u', ?, ?, ?)""",
        [(rng.choice(pids), f'e{i}', rng.choice([0, 0, 5]),
          f'2026-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}T00:00:00Z',
          f'2026-01-01T00:{i // 60 % 60:02d}:{i % 60:02d}Z') for i in range(5000)])
    conn.commit()
    started = time.perf_counter()
    claimed = temp_db.claim_next_queued_episode()
    elapsed_ms = (time.perf_counter() - started) * 1000
    with capsys.disabled():
        print(f"\n5000-row claim: {elapsed_ms:.1f} ms")
    assert claimed is not None
