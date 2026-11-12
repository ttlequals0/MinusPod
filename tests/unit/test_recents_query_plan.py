"""The Recents feed query must walk episodes by (status, published_at) instead of sorting every processed row."""
from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('recents_plan_test_')

import pytest  # noqa: E402

import database  # noqa: E402

_SLUG = 'plan-source'
_ROWS = 500


@pytest.fixture
def seeded_db():
    db = database.Database()
    db.create_podcast(_SLUG, 'https://example.com/plan-source.xml', 'Plan Source', feed_type='subscribed')
    pid = db.get_podcast_by_slug(_SLUG)['id']
    conn = db.get_connection()
    conn.executemany(
        "INSERT INTO episodes (podcast_id, episode_id, original_url, title, status, processed_file, published_at) "
        "VALUES (?, ?, ?, ?, ?, '/x.mp3', ?)",
        [(pid, f'e{n:010d}', f'https://example.com/{n}.mp3', f'Episode {n}',
          'processed' if n % 10 else 'pending', f'20{10 + n % 15}-{1 + n % 12:02d}-{1 + n % 28:02d}T00:00:00Z')
         for n in range(_ROWS)])
    conn.commit()
    yield db
    db.delete_podcast(_SLUG)


def _plan(db):
    plan = db.get_connection().execute(
        "EXPLAIN QUERY PLAN SELECT e.* " + db._RECENTS_WHERE
        + " ORDER BY e.published_at DESC, e.id DESC LIMIT 100", ['2000-01-01']).fetchall()
    return ' '.join(row[-1] for row in plan)


def test_recents_query_uses_the_status_published_index(seeded_db):
    text = _plan(seeded_db)
    assert 'idx_episodes_status_published' in text
    assert 'USE TEMP B-TREE' not in text


def test_many_episodes_sharing_one_publish_time_need_no_temp_sort(seeded_db):
    pid = seeded_db.get_podcast_by_slug(_SLUG)['id']
    conn = seeded_db.get_connection()
    conn.executemany(
        "INSERT INTO episodes (podcast_id, episode_id, original_url, title, status, processed_file, published_at) "
        "VALUES (?, ?, ?, ?, 'processed', '/x.mp3', '2026-01-01T00:00:00Z')",
        [(pid, f'same{n:06d}', f'https://example.com/same/{n}.mp3', f'Same {n}') for n in range(2000)])
    conn.commit()
    text = _plan(seeded_db)
    assert 'idx_episodes_status_published' in text
    assert 'USE TEMP B-TREE' not in text


def test_index_exists_on_a_fresh_database(seeded_db):
    names = {r[0] for r in seeded_db.get_connection().execute(
        "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'episodes'")}
    assert 'idx_episodes_status_published' in names
