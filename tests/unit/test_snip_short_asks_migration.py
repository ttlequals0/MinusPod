"""Existing podcasts rows gain snip_short_asks as NULL (inherit) without data loss."""
from tests.unit.db_test_utils import _seed


def _columns(conn):
    return {r[1] for r in conn.execute("PRAGMA table_info(podcasts)")}


def test_column_added_to_existing_rows_as_null(temp_db):
    _seed(temp_db)
    conn = temp_db.get_connection()
    conn.execute("ALTER TABLE podcasts DROP COLUMN snip_short_asks")
    conn.commit()
    assert 'snip_short_asks' not in _columns(conn)
    before = conn.execute("SELECT slug, source_url, title FROM podcasts").fetchall()
    assert before

    temp_db._run_schema_migrations()

    assert 'snip_short_asks' in _columns(conn)
    assert conn.execute("SELECT slug, source_url, title FROM podcasts").fetchall() == before
    assert {r[0] for r in conn.execute("SELECT snip_short_asks FROM podcasts")} == {None}
