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


def test_a_daily_same_title_pair_24h_apart_is_not_collapsed(temp_db):
    """A daily show's same-titled episodes 24h apart are not a dropped
    timezone offset; they must stay distinct rows."""
    slug = _seed_podcast(temp_db)
    temp_db.upsert_episode(
        slug, 'dailyA0000001', title='Daily Show', published_at='2026-01-01T08:00:00Z',
        original_url='https://example.com/day1.mp3', status='discovered')
    temp_db.upsert_episode(
        slug, 'dailyB0000001', title='Daily Show', published_at='2026-01-02T08:00:00Z',
        original_url='https://example.com/day2.mp3', status='discovered')

    _run(temp_db)

    assert temp_db.get_episode(slug, 'dailyA0000001') is not None
    assert temp_db.get_episode(slug, 'dailyB0000001') is not None


def test_a_non_quarter_hour_drift_is_not_collapsed(temp_db):
    slug = _seed_podcast(temp_db)
    temp_db.upsert_episode(
        slug, 'offstep0000001', title='Off Step Episode', published_at='2026-01-01T00:00:00Z',
        original_url='https://example.com/a.mp3', status='discovered')
    temp_db.upsert_episode(
        slug, 'offstep0000002', title='Off Step Episode', published_at='2026-01-01T07:05:00Z',
        original_url='https://example.com/b.mp3', status='discovered')

    _run(temp_db)

    assert temp_db.get_episode(slug, 'offstep0000001') is not None
    assert temp_db.get_episode(slug, 'offstep0000002') is not None


def test_cluster_compares_to_first_member_not_the_running_last(temp_db):
    """Three same-title rows at +0h/+10h/+20h: each adjacent pair is within
    the drift window, but the first and last are 20h apart - not real
    drift. Clustering must anchor to the first member, or the last row
    gets swept into the first row's cluster and wrongly deleted too."""
    slug = _seed_podcast(temp_db)
    temp_db.upsert_episode(
        slug, 'chainA0000001', title='Chain Episode', published_at='2026-01-01T00:00:00Z',
        original_url='https://example.com/a.mp3', status='discovered')
    temp_db.upsert_episode(
        slug, 'chainB0000001', title='Chain Episode', published_at='2026-01-01T10:00:00Z',
        original_url='https://example.com/b.mp3', status='discovered')
    temp_db.upsert_episode(
        slug, 'chainC0000001', title='Chain Episode', published_at='2026-01-01T20:00:00Z',
        original_url='https://example.com/c.mp3', status='discovered')

    _run(temp_db)

    assert temp_db.get_episode(slug, 'chainA0000001') is not None
    assert temp_db.get_episode(slug, 'chainB0000001') is None
    assert temp_db.get_episode(slug, 'chainC0000001') is not None


def test_a_discovered_duplicate_with_processing_history_is_kept(temp_db):
    slug = _seed_podcast(temp_db)
    temp_db.upsert_episode(
        slug, 'historyrow00001', title='History Episode', published_at='2026-01-01T00:00:00Z',
        original_url='https://example.com/a.mp3', status='discovered')
    temp_db.upsert_episode(
        slug, 'bareorphan00001', title='History Episode', published_at='2026-01-01T05:00:00Z',
        original_url='https://example.com/b.mp3', status='discovered')
    podcast = temp_db.get_podcast_by_slug(slug)
    conn = temp_db.get_connection()
    conn.execute(
        "INSERT INTO processing_history (podcast_id, podcast_slug, episode_id, status) "
        "VALUES (?, ?, ?, 'completed')",
        (podcast['id'], slug, 'historyrow00001'))
    conn.commit()

    _run(temp_db)

    assert temp_db.get_episode(slug, 'historyrow00001') is not None
    assert temp_db.get_episode(slug, 'bareorphan00001') is None


def test_a_discovered_duplicate_with_passthrough_override_is_kept(temp_db):
    slug = _seed_podcast(temp_db)
    temp_db.upsert_episode(
        slug, 'passthrukeep01', title='Passthrough Episode', published_at='2026-01-01T00:00:00Z',
        original_url='https://example.com/a.mp3', status='discovered')
    temp_db.upsert_episode(
        slug, 'passthrubare01', title='Passthrough Episode', published_at='2026-01-01T05:00:00Z',
        original_url='https://example.com/b.mp3', status='discovered')
    temp_db.set_episodes_passthrough(slug, ['passthrukeep01'], True)

    _run(temp_db)

    assert temp_db.get_episode(slug, 'passthrukeep01') is not None
    assert temp_db.get_episode(slug, 'passthrubare01') is None


def test_deleted_duplicate_rows_disappear_from_the_search_index(temp_db):
    slug = _seed_podcast(temp_db)
    temp_db.upsert_episode(
        slug, 'indexkept000001', title='Indexed Title', published_at='2026-01-01T00:00:00Z',
        original_url='https://example.com/old.mp3', status='processed')
    temp_db.upsert_episode(
        slug, 'indexorphan0001', title='Indexed Title', published_at='2026-01-01T07:00:00Z',
        original_url='https://example.com/new.mp3', status='discovered')

    conn = _run(temp_db)

    orphan_count = conn.execute(
        "SELECT COUNT(*) AS c FROM search_index "
        "WHERE content_type = 'episode' AND content_id = ?",
        ('indexorphan0001',)).fetchone()['c']
    kept_count = conn.execute(
        "SELECT COUNT(*) AS c FROM search_index "
        "WHERE content_type = 'episode' AND content_id = ?",
        ('indexkept000001',)).fetchone()['c']
    assert orphan_count == 0
    assert kept_count == 1
