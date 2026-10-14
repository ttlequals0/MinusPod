"""Existing podcasts rows gain the description trim columns without data loss."""
from tests.unit.db_test_utils import _seed


def _drop_trim_columns(conn):
    conn.execute("ALTER TABLE podcasts DROP COLUMN description_trim_pattern")
    conn.execute("ALTER TABLE podcasts DROP COLUMN description_trim_mode")
    conn.commit()


def _columns(conn):
    return {r[1] for r in conn.execute("PRAGMA table_info(podcasts)")}


def test_columns_added_to_existing_rows_with_null_pattern(temp_db):
    _seed(temp_db)
    conn = temp_db.get_connection()
    _drop_trim_columns(conn)
    assert 'description_trim_pattern' not in _columns(conn)

    temp_db._run_schema_migrations()

    assert {'description_trim_pattern', 'description_trim_mode'} <= _columns(conn)
    row = conn.execute(
        "SELECT description_trim_pattern, description_trim_mode FROM podcasts").fetchone()
    assert row[0] is None
    assert row[1] == 'to_end'


def test_chapter_notes_column_added_to_existing_rows_as_null(temp_db):
    _seed(temp_db)
    conn = temp_db.get_connection()
    conn.execute("ALTER TABLE podcasts DROP COLUMN chapter_notes")
    conn.commit()

    temp_db._run_schema_migrations()

    assert 'chapter_notes' in _columns(conn)
    assert conn.execute("SELECT chapter_notes FROM podcasts").fetchone()[0] is None
