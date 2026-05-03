"""Test the 2.0.19 -> 2.0.20 migration of
podcasts.only_expose_processed_episodes from INTEGER DEFAULT 0 to plain
nullable INTEGER.

Setup: build a stripped-down podcasts table at the OLD schema, insert
rows with values 1 and 0, then call _run_schema_migrations and assert
the column is now nullable with the expected values.
"""

import sqlite3

import pytest


@pytest.fixture
def legacy_db_path(tmp_path):
    """Build a SQLite file that mimics the 2.0.19 podcasts schema."""
    path = tmp_path / 'podcast.db'
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE podcasts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            slug TEXT UNIQUE NOT NULL,
            source_url TEXT NOT NULL,
            title TEXT,
            only_expose_processed_episodes INTEGER DEFAULT 0
        )
    """)
    conn.execute(
        "INSERT INTO podcasts (slug, source_url, title, only_expose_processed_episodes) "
        "VALUES (?, ?, ?, ?)",
        ('keep-on', 'https://example.com/a.xml', 'Keep On', 1),
    )
    conn.execute(
        "INSERT INTO podcasts (slug, source_url, title, only_expose_processed_episodes) "
        "VALUES (?, ?, ?, ?)",
        ('use-global', 'https://example.com/b.xml', 'Use Global', 0),
    )
    conn.commit()
    conn.close()
    return path


def _column_info(conn, table, column):
    cursor = conn.execute(f"PRAGMA table_info({table})")
    for row in cursor:
        if row['name'] == column:
            return row
    return None


def test_migration_preserves_explicit_one_and_nulls_zeros(legacy_db_path):
    # Inline the migration logic so the test stands alone (does not depend
    # on the singleton Database init path).
    conn = sqlite3.connect(legacy_db_path)
    conn.row_factory = sqlite3.Row

    pre_col = _column_info(conn, 'podcasts', 'only_expose_processed_episodes')
    assert pre_col is not None
    assert pre_col['dflt_value'] is not None  # has DEFAULT 0

    # Mirror the 4-step ALTER chain from src/database/schema.py.
    conn.execute(
        "ALTER TABLE podcasts ADD COLUMN only_expose_processed_episodes_v2 INTEGER"
    )
    conn.execute(
        "UPDATE podcasts SET only_expose_processed_episodes_v2 = "
        "CASE WHEN only_expose_processed_episodes = 1 THEN 1 ELSE NULL END"
    )
    conn.execute("ALTER TABLE podcasts DROP COLUMN only_expose_processed_episodes")
    conn.execute(
        "ALTER TABLE podcasts RENAME COLUMN "
        "only_expose_processed_episodes_v2 TO only_expose_processed_episodes"
    )
    conn.commit()

    post_col = _column_info(conn, 'podcasts', 'only_expose_processed_episodes')
    assert post_col is not None
    assert post_col['dflt_value'] is None  # no DEFAULT after migration

    rows = conn.execute(
        "SELECT slug, only_expose_processed_episodes FROM podcasts ORDER BY slug"
    ).fetchall()
    by_slug = {r['slug']: r['only_expose_processed_episodes'] for r in rows}
    assert by_slug == {'keep-on': 1, 'use-global': None}

    # Round-trip a third value (NULL stays NULL on insert without value).
    conn.execute(
        "INSERT INTO podcasts (slug, source_url, title) VALUES (?, ?, ?)",
        ('fresh', 'https://example.com/c.xml', 'Fresh'),
    )
    conn.commit()
    fresh = conn.execute(
        "SELECT only_expose_processed_episodes FROM podcasts WHERE slug = 'fresh'"
    ).fetchone()
    assert fresh['only_expose_processed_episodes'] is None

    conn.close()


def test_full_migration_via_database_init(legacy_db_path, monkeypatch):
    """Sanity check: pointing the real Database class at the legacy DB
    runs _run_schema_migrations and produces a nullable column.
    """
    from database import Database

    Database._instance = None
    monkeypatch.setenv('DATA_DIR', str(legacy_db_path.parent))
    db = Database(data_dir=str(legacy_db_path.parent))

    conn = db.get_connection()
    post_col = _column_info(conn, 'podcasts', 'only_expose_processed_episodes')
    assert post_col is not None
    assert post_col['dflt_value'] is None

    # keep-on row preserved as 1, use-global row coerced to NULL.
    rows = conn.execute(
        "SELECT slug, only_expose_processed_episodes FROM podcasts "
        "WHERE slug IN ('keep-on', 'use-global')"
    ).fetchall()
    by_slug = {r['slug']: r['only_expose_processed_episodes'] for r in rows}
    assert by_slug == {'keep-on': 1, 'use-global': None}

    Database._instance = None
