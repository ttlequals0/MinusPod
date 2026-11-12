"""The ordered-pending query skips the window CTE when every pending feed is on 'queued'."""
import random

from database.queue import _ORDERED_BY
from tests.unit.test_queue_order import _feed, _row, _order


def _claim_plan(db):
    cte, params = db._ordered_pending()
    rows = db.get_connection().execute(
        f"""EXPLAIN QUERY PLAN {cte}
           SELECT q.*, p.slug FROM ordered o
           JOIN auto_process_queue q ON q.id = o.id
           JOIN podcasts p ON q.podcast_id = p.id
           ORDER BY {_ORDERED_BY} LIMIT 1""",  # noqa: S608
        params).fetchall()
    return "\n".join(r['detail'] for r in rows)


def _seed(db, feeds, count, seed=1):
    rng = random.Random(seed)
    pids = [_feed(db, f'f{i}') for i in range(feeds)]
    for i in range(count):
        _row(db, rng.choice(pids), f'e{i}', f'2026-{rng.randint(1, 12):02d}-10T00:00:00Z',
             priority=rng.choice([0, 0, 5, 10]), created=f'2026-01-01T00:{i // 60 % 60:02d}:{i % 60:02d}Z')


def test_all_queued_feeds_skip_the_window_sort(temp_db):
    _seed(temp_db, 3, 40)
    plan = _claim_plan(temp_db)
    assert 'TEMP B-TREE' not in plan, plan
    assert 'idx_queue_status_priority' in plan, plan


def test_a_feed_override_uses_the_window_cte(temp_db):
    _seed(temp_db, 3, 40)
    temp_db.get_connection().execute("UPDATE podcasts SET queue_order = 'oldest_first' WHERE slug = 'f1'")
    temp_db.get_connection().commit()
    assert 'TEMP B-TREE' in _claim_plan(temp_db)


def test_a_global_order_uses_the_window_cte(temp_db):
    _seed(temp_db, 3, 40)
    temp_db.set_setting('queue_order', 'newest_first', is_default=False)
    assert 'TEMP B-TREE' in _claim_plan(temp_db)


def test_an_override_on_a_feed_with_nothing_pending_keeps_the_fast_path(temp_db):
    _seed(temp_db, 2, 20)
    _feed(temp_db, 'idle', 'newest_first')
    assert 'TEMP B-TREE' not in _claim_plan(temp_db)


def test_both_paths_give_the_same_order(temp_db, monkeypatch):
    _seed(temp_db, 4, 300, seed=7)
    fast = _order(temp_db)
    claimed_fast = temp_db.get_next_queued_episode()['id']
    monkeypatch.setattr(type(temp_db), '_window_order_needed', lambda self, *args: True)
    assert 'TEMP B-TREE' in _claim_plan(temp_db)
    assert _order(temp_db) == fast
    assert temp_db.get_next_queued_episode()['id'] == claimed_fast


def test_exclusions_work_on_the_fast_path(temp_db):
    _seed(temp_db, 2, 10)
    first = temp_db.get_pending_queued_episodes(limit=1)[0]
    claimed = temp_db.claim_next_queued_episode(
        exclude_episodes=[(first['podcast_slug'], first['episode_id'])])
    assert claimed['episode_id'] != first['episode_id']
