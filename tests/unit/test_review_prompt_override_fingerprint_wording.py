"""A stored review_prompt_override loses the stale sentence and nothing else."""

from database import Database

OLD = "fingerprint spans are projected lengths, so prefer the transcript's precise edges."
NEW = ("fingerprint spans are measured audio matches, but the transcript's precise "
       "edges are still preferred where they extend the span.")
GATE = 'review_prompt_override_fingerprint_measured'


def _store(db, value):
    conn = db.get_connection()
    conn.execute("INSERT OR REPLACE INTO settings (key, value, is_default) "
                 "VALUES ('review_prompt_override', ?, 0)", (value,))
    conn.execute("DELETE FROM schema_migrations WHERE name = ?", (GATE,))
    conn.commit()


def _stored(db):
    row = db.get_connection().execute(
        "SELECT value FROM settings WHERE key = 'review_prompt_override'").fetchone()
    return row['value']


def _run(db):
    db._run_review_prompt_override_fingerprint_wording(db.get_connection())


def test_override_with_sentence_has_only_it_replaced(temp_db):
    custom = f"Extra rule.\n{OLD}\nKeep {OLD[:20]} this."
    _store(temp_db, custom)
    _run(temp_db)
    assert _stored(temp_db) == custom.replace(OLD, NEW)


def test_override_without_sentence_is_byte_identical(temp_db):
    custom = "Only my words, é and trailing space \n"
    _store(temp_db, custom)
    _run(temp_db)
    assert _stored(temp_db) == custom


def test_empty_override_is_untouched(temp_db):
    _store(temp_db, "")
    _run(temp_db)
    assert _stored(temp_db) == ""


def test_missing_override_is_fine(temp_db):
    conn = temp_db.get_connection()
    conn.execute("DELETE FROM settings WHERE key = 'review_prompt_override'")
    conn.execute("DELETE FROM schema_migrations WHERE name = ?", (GATE,))
    conn.commit()
    _run(temp_db)
    assert conn.execute("SELECT 1 FROM schema_migrations WHERE name = ?", (GATE,)).fetchone()


def test_step_runs_once(temp_db):
    _store(temp_db, f"a {OLD} b")
    _run(temp_db)
    temp_db.get_connection().execute(
        "UPDATE settings SET value = ? WHERE key = 'review_prompt_override'", (f"again {OLD}",))
    temp_db.get_connection().commit()
    _run(temp_db)
    assert _stored(temp_db) == f"again {OLD}"


def test_second_start_changes_nothing(tmp_path):
    path = str(tmp_path / 'p.db')
    Database._instance = None
    _store(Database(path), f"a {OLD} b")
    Database._instance = None
    first = _stored(Database(path))
    Database._instance = None
    second = _stored(Database(path))
    Database._instance = None
    assert first == second == f"a {NEW} b"
