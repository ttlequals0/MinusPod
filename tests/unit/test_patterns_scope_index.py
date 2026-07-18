"""idx_patterns_scope creation (fresh schema + migration).

The index was defined in the retired MIGRATION_INDEXES_SQL constant but never
executed anywhere, so no database ever had it. It is now part of SCHEMA_SQL
for fresh databases and created by an idempotent migration step for existing
ones. These tests cover both paths.
"""
import pytest

from database import Database

INDEX_NAME = 'idx_patterns_scope'
EXPECTED_COLUMNS = '(scope, network_id, podcast_id)'


def _index_sql(conn):
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND name = ?",
        (INDEX_NAME,),
    ).fetchone()
    return row[0] if row else None


@pytest.fixture
def scoped_db(tmp_path):
    Database._instance = None
    yield tmp_path
    Database._instance = None


def test_fresh_db_has_idx_patterns_scope(scoped_db):
    db = Database(data_dir=str(scoped_db))
    sql = _index_sql(db.get_connection())
    assert sql is not None
    assert EXPECTED_COLUMNS in sql
    assert 'WHERE is_active = 1' in sql


def test_existing_db_gains_idx_patterns_scope_on_migrate(scoped_db):
    # Build a database, then drop the index to simulate the pre-migration
    # state every existing install is in (the index never existed anywhere).
    db = Database(data_dir=str(scoped_db))
    conn = db.get_connection()
    conn.execute(f"DROP INDEX {INDEX_NAME}")
    conn.commit()
    assert _index_sql(conn) is None

    # Re-open: the existing-database path runs the migration ladder, which
    # must recreate the index.
    Database._instance = None
    db2 = Database(data_dir=str(scoped_db))
    sql = _index_sql(db2.get_connection())
    assert sql is not None
    assert EXPECTED_COLUMNS in sql
