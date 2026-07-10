"""Migration test for the 'deferred' episodes status CHECK rebuild (#482).

Builds a DB whose episodes table has the pre-deferred CHECK, inserts episodes
in every legacy status, points the real Database class at it, and asserts the
rebuild preserved every row while allowing 'deferred' inserts afterward.
"""
import sqlite3

import pytest

LEGACY_STATUSES = ['discovered', 'pending', 'processing', 'processed',
                   'failed', 'permanently_failed']


@pytest.fixture
def legacy_db_path(tmp_path):
    path = tmp_path / 'podcast.db'
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE podcasts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            slug TEXT UNIQUE NOT NULL,
            source_url TEXT NOT NULL,
            title TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE episodes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            podcast_id INTEGER NOT NULL,
            episode_id TEXT NOT NULL,
            original_url TEXT NOT NULL,
            title TEXT,
            description TEXT,
            status TEXT DEFAULT 'pending' CHECK(status IN ('discovered','pending','processing','processed','failed','permanently_failed')),
            retry_count INTEGER DEFAULT 0,
            error_message TEXT,
            tags TEXT NOT NULL DEFAULT '[]',
            created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
            updated_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
            FOREIGN KEY (podcast_id) REFERENCES podcasts(id) ON DELETE CASCADE,
            UNIQUE(podcast_id, episode_id)
        )
    """)
    conn.execute(
        "INSERT INTO podcasts (slug, source_url, title) VALUES (?, ?, ?)",
        ('migration-feed', 'https://example.com/feed.xml', 'Migration Feed'),
    )
    for i, status in enumerate(LEGACY_STATUSES):
        conn.execute(
            "INSERT INTO episodes (podcast_id, episode_id, original_url, title, status, retry_count) "
            "VALUES (1, ?, ?, ?, ?, ?)",
            (f'ep-{i}', f'https://example.com/ep{i}.mp3', f'Episode {i}', status, i),
        )
    conn.commit()
    conn.close()
    return path


def test_deferred_migration_preserves_rows_and_allows_deferred(legacy_db_path, monkeypatch):
    from database import Database

    Database._instance = None
    db = Database(data_dir=str(legacy_db_path.parent))
    conn = db.get_connection()

    create_sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='episodes'"
    ).fetchone()[0]
    assert "'deferred'" in create_sql

    cols = {row['name'] for row in conn.execute("PRAGMA table_info(episodes)")}
    assert 'deferred_at' in cols
    assert 'deferred_service' in cols

    rows = conn.execute(
        "SELECT episode_id, status, retry_count FROM episodes ORDER BY episode_id"
    ).fetchall()
    assert len(rows) == len(LEGACY_STATUSES)
    for i, row in enumerate(rows):
        assert row['status'] == LEGACY_STATUSES[i]
        assert row['retry_count'] == i

    conn.execute(
        "INSERT INTO episodes (podcast_id, episode_id, original_url, status, deferred_at, deferred_service) "
        "SELECT id, 'ep-deferred', 'https://example.com/d.mp3', 'deferred', "
        "'2026-01-01T00:00:00Z', 'llm' FROM podcasts WHERE slug = 'migration-feed'"
    )
    conn.commit()
    deferred = conn.execute(
        "SELECT status, deferred_service FROM episodes WHERE episode_id = 'ep-deferred'"
    ).fetchone()
    assert deferred['status'] == 'deferred'
    assert deferred['deferred_service'] == 'llm'

    Database._instance = None
