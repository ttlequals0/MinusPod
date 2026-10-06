"""Tests for the _dedup_orphan_discovered_episodes migration: it removes a
'discovered' duplicate row left behind by a stale pre-fix published_at that
missed the exact title+date GUID-change match (see episodes.py's fuzzy
match), without ever deleting a row that carries real processing state."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

GATE = 'dedup_orphan_discovered_episodes_v1'


def _seed_podcast(temp_db, slug='dedup-test'):
    temp_db.create_podcast(slug, 'https://example.com/feed.xml', 'Dedup Test')
    return slug


def _run(temp_db):
    """Clear the gate the boot-time run set, then run the migration."""
    conn = temp_db.get_connection()
    conn.execute("DELETE FROM schema_migrations WHERE name = ?", (GATE,))
    conn.commit()
    temp_db._dedup_orphan_discovered_episodes(conn)
    return conn


def test_removes_stale_duplicate_of_a_completed_episode(temp_db):
    slug = _seed_podcast(temp_db)
    temp_db.upsert_episode(
        slug, 'completed0001', title='Shared Title', published_at='2026-01-01T00:00:00Z',
        original_url='https://example.com/old.mp3', status='processed')
    temp_db.upsert_episode(
        slug, 'orphan0000001', title='Shared Title', published_at='2026-01-01T07:00:00Z',
        original_url='https://example.com/new.mp3', status='discovered')

    _run(temp_db)

    assert temp_db.get_episode(slug, 'completed0001') is not None
    assert temp_db.get_episode(slug, 'orphan0000001') is None


def test_two_legit_same_title_episodes_a_week_apart_are_both_kept(temp_db):
    slug = _seed_podcast(temp_db)
    temp_db.upsert_episode(
        slug, 'weekly0000001', title='Weekly Recap', published_at='2026-01-01T00:00:00Z',
        original_url='https://example.com/one.mp3', status='discovered')
    temp_db.upsert_episode(
        slug, 'weekly0000002', title='Weekly Recap', published_at='2026-01-08T00:00:00Z',
        original_url='https://example.com/two.mp3', status='discovered')

    _run(temp_db)

    assert temp_db.get_episode(slug, 'weekly0000001') is not None
    assert temp_db.get_episode(slug, 'weekly0000002') is not None


def test_a_duplicate_that_has_markers_is_kept_not_deleted(temp_db):
    slug = _seed_podcast(temp_db)
    temp_db.upsert_episode(
        slug, 'bare0000001', title='Marked Episode', published_at='2026-01-01T00:00:00Z',
        original_url='https://example.com/bare.mp3', status='discovered')
    temp_db.upsert_episode(
        slug, 'marked0000001', title='Marked Episode', published_at='2026-01-01T05:00:00Z',
        original_url='https://example.com/marked.mp3', status='discovered')
    temp_db.save_episode_details(
        slug, 'marked0000001',
        ad_markers=[{'start': 10.0, 'end': 20.0, 'was_cut': True}])

    _run(temp_db)

    assert temp_db.get_episode(slug, 'bare0000001') is None
    assert temp_db.get_episode(slug, 'marked0000001') is not None


def test_when_neither_row_has_state_the_older_row_is_kept(temp_db):
    slug = _seed_podcast(temp_db)
    temp_db.upsert_episode(
        slug, 'older0000001', title='No State Episode', published_at='2026-01-01T00:00:00Z',
        original_url='https://example.com/older.mp3', status='discovered')
    temp_db.upsert_episode(
        slug, 'newer0000001', title='No State Episode', published_at='2026-01-01T06:00:00Z',
        original_url='https://example.com/newer.mp3', status='discovered')

    _run(temp_db)

    assert temp_db.get_episode(slug, 'older0000001') is not None
    assert temp_db.get_episode(slug, 'newer0000001') is None


def test_the_migration_runs_once(temp_db):
    slug = _seed_podcast(temp_db)
    temp_db.upsert_episode(
        slug, 'completed0002', title='Gate Title', published_at='2026-01-01T00:00:00Z',
        original_url='https://example.com/old.mp3', status='processed')
    temp_db.upsert_episode(
        slug, 'orphan0000002', title='Gate Title', published_at='2026-01-01T07:00:00Z',
        original_url='https://example.com/new.mp3', status='discovered')

    conn = _run(temp_db)
    gate = conn.execute(
        "SELECT 1 FROM schema_migrations WHERE name = ?", (GATE,)).fetchone()
    assert gate is not None

    # A second call is a no-op even with a fresh orphan duplicate present.
    temp_db.upsert_episode(
        slug, 'orphan0000003', title='Gate Title', published_at='2026-01-01T08:00:00Z',
        original_url='https://example.com/newer.mp3', status='discovered')
    temp_db._dedup_orphan_discovered_episodes(conn)

    assert temp_db.get_episode(slug, 'orphan0000003') is not None
