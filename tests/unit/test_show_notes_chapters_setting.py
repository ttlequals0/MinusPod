"""show_notes_chapters per-feed column: migration, resolver and config transfer."""
from config import resolve_show_notes_chapters
from tests.unit.db_test_utils import _seed


def _columns(conn):
    return {r[1] for r in conn.execute("PRAGMA table_info(podcasts)")}


def test_column_added_to_existing_rows_as_null(temp_db):
    _seed(temp_db)
    conn = temp_db.get_connection()
    conn.execute("ALTER TABLE podcasts DROP COLUMN show_notes_chapters")
    conn.commit()
    before = conn.execute("SELECT slug, source_url, title FROM podcasts").fetchall()
    assert before

    temp_db._run_schema_migrations()

    assert 'show_notes_chapters' in _columns(conn)
    assert conn.execute("SELECT slug, source_url, title FROM podcasts").fetchall() == before
    assert {r[0] for r in conn.execute("SELECT show_notes_chapters FROM podcasts")} == {None}


def test_null_and_missing_rows_resolve_on_and_zero_resolves_off():
    assert resolve_show_notes_chapters(None) is True
    assert resolve_show_notes_chapters({'show_notes_chapters': None}) is True
    assert resolve_show_notes_chapters({'show_notes_chapters': 1}) is True
    assert resolve_show_notes_chapters({'show_notes_chapters': 0}) is False


def test_feed_column_is_transferred_as_a_bool():
    import config_transfer
    assert 'show_notes_chapters' in config_transfer.FEED_COLUMNS
    assert 'show_notes_chapters' in config_transfer.FEED_BOOL_COLUMNS
