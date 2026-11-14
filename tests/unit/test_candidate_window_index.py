"""idx_episodes_processed_at backs the newest-first candidate window on fresh and migrated databases."""
from database import Database

INDEX = 'idx_episodes_processed_at'


def _has_index(db):
    return INDEX in {r[1] for r in db.get_connection().execute("PRAGMA index_list('episodes')")}


def test_fresh_database_has_the_index(temp_db):
    assert _has_index(temp_db)


def test_existing_database_gets_the_index_back(temp_db, temp_dir):
    temp_db.get_connection().execute(f'DROP INDEX {INDEX}')
    temp_db.get_connection().commit()
    assert not _has_index(temp_db)
    reopened = object.__new__(Database)
    reopened._initialized = False
    Database.__init__(reopened, data_dir=temp_dir)
    assert _has_index(reopened)


def test_global_window_scans_the_index_not_the_table(temp_db):
    statements = []
    conn = temp_db.get_connection()
    conn.set_trace_callback(lambda sql: statements.append(sql) if 'JOIN podcasts' in sql else None)
    temp_db.get_pattern_transcript_episodes({'id': 1, 'scope': 'global'}, 10)
    conn.set_trace_callback(None)
    plan = ' '.join(r[3] for r in conn.execute('EXPLAIN QUERY PLAN ' + statements[-1]))
    assert f'SCAN e USING INDEX {INDEX}' in plan
