"""Migration test for episodes.passthrough_enabled (#746).

Builds a DB at the pre-#746 episodes schema (column absent), inserts rows,
then points the Database class at it and asserts the column is added
additively with no data loss to existing rows.
"""
import re
import sqlite3

import pytest

from database.schema.tables import TABLE_DDL


@pytest.fixture
def legacy_db_path(tmp_path):
    path = tmp_path / 'podcast.db'
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute(TABLE_DDL['podcasts'])
    legacy_episodes_ddl = re.sub(
        r"\n\s*-- Per-episode pass-through override, issue #746\.\n\s*"
        r"passthrough_enabled INTEGER,\n",
        "\n", TABLE_DDL['episodes']
    )
    assert 'passthrough_enabled' not in legacy_episodes_ddl
    conn.execute(legacy_episodes_ddl)
    conn.execute(TABLE_DDL['episode_details'])
    conn.execute(TABLE_DDL['settings'])
    conn.execute(TABLE_DDL['stats'])
    conn.execute(
        "INSERT INTO podcasts (slug, source_url, title) VALUES (?, ?, ?)",
        ('migration-feed', 'https://example.com/feed.xml', 'Migration Feed'),
    )
    conn.execute(
        "INSERT INTO episodes (podcast_id, episode_id, original_url, status, title) "
        "VALUES (1, 'ep-1', 'https://example.com/ep1.mp3', 'processed', 'Episode 1')"
    )
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def legacy_db(legacy_db_path):
    from database import Database

    Database._instance = None
    db = Database(data_dir=str(legacy_db_path.parent))
    yield db
    Database._instance = None


def _column_info(conn, table, column):
    for row in conn.execute(f"PRAGMA table_info({table})"):
        if row['name'] == column:
            return row
    return None


def test_column_added_and_existing_row_preserved(legacy_db):
    conn = legacy_db.get_connection()

    col = _column_info(conn, 'episodes', 'passthrough_enabled')
    assert col is not None
    assert col['notnull'] == 0  # nullable, no forced default

    row = conn.execute(
        "SELECT episode_id, title, status, passthrough_enabled "
        "FROM episodes WHERE episode_id = 'ep-1'"
    ).fetchone()
    assert row['title'] == 'Episode 1'
    assert row['status'] == 'processed'
    assert row['passthrough_enabled'] is None


def test_migration_is_idempotent_on_second_init(legacy_db_path):
    from database import Database

    Database._instance = None
    db = Database(data_dir=str(legacy_db_path.parent))
    db.set_episodes_passthrough('migration-feed', ['ep-1'], True)
    Database._instance = None

    db2 = Database(data_dir=str(legacy_db_path.parent))
    conn = db2.get_connection()
    row = conn.execute(
        "SELECT passthrough_enabled FROM episodes WHERE episode_id = 'ep-1'"
    ).fetchone()
    assert row['passthrough_enabled'] == 1
    Database._instance = None


def test_fresh_database_has_column():
    """A brand-new DB (SCHEMA_SQL path, not the migration loop) also has
    the column, via the CREATE TABLE DDL in tables.py."""
    assert 'passthrough_enabled' in TABLE_DDL['episodes']
