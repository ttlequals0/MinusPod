"""Stored review prompts lose the stale "projected lengths" sentence and nothing else."""

from database import DEFAULT_REVIEW_PROMPT, Database

OLD = "fingerprint spans are projected lengths, so prefer the transcript's precise edges."
NEW = ("fingerprint spans are measured audio matches, but the transcript's precise "
       "edges are still preferred where they extend the span.")
GATE = 'review_prompt_fingerprint_measured'


def _store(db, value, is_default):
    conn = db.get_connection()
    conn.execute("UPDATE settings SET value = ?, is_default = ? WHERE key = 'review_prompt'",
                 (value, int(is_default)))
    conn.execute("DELETE FROM schema_migrations WHERE name = ?", (GATE,))
    conn.commit()


def _stored(db):
    return db.get_connection().execute(
        "SELECT value, is_default FROM settings WHERE key = 'review_prompt'").fetchone()


def _run(db):
    db._run_review_prompt_fingerprint_wording(db.get_connection())


def test_default_prompt_uses_the_new_sentence():
    assert NEW in DEFAULT_REVIEW_PROMPT and OLD not in DEFAULT_REVIEW_PROMPT


def test_stored_default_is_updated(temp_db):
    _store(temp_db, DEFAULT_REVIEW_PROMPT.replace(NEW, OLD), True)
    _run(temp_db)
    row = _stored(temp_db)
    assert row['value'] == DEFAULT_REVIEW_PROMPT and row['is_default'] == 1


def test_customized_prompt_has_only_the_sentence_replaced(temp_db):
    custom = f"My intro.\n\n{OLD}\n\nMy outro with {OLD[:20]} kept."
    _store(temp_db, custom, False)
    _run(temp_db)
    row = _stored(temp_db)
    assert row['value'] == custom.replace(OLD, NEW) and row['is_default'] == 0


def test_customized_prompt_without_the_sentence_is_untouched(temp_db):
    custom = "Only my words, é and trailing space \n"
    _store(temp_db, custom, False)
    _run(temp_db)
    assert _stored(temp_db)['value'] == custom


def test_step_runs_once(temp_db):
    _store(temp_db, f"a {OLD} b", False)
    _run(temp_db)
    _store_after = _stored(temp_db)['value']
    temp_db.get_connection().execute("UPDATE settings SET value = ? WHERE key = 'review_prompt'",
                                     (f"again {OLD}",))
    temp_db.get_connection().commit()
    _run(temp_db)
    assert _store_after == f"a {NEW} b"
    assert _stored(temp_db)['value'] == f"again {OLD}"


def test_second_start_changes_nothing(tmp_path):
    path = str(tmp_path / 'p.db')
    Database._instance = None
    db = Database(path)
    _store(db, f"a {OLD} b", False)
    Database._instance = None
    first = _stored(Database(path))['value']
    Database._instance = None
    second = _stored(Database(path))['value']
    Database._instance = None
    assert first == second == f"a {NEW} b"


def test_missing_setting_is_fine(temp_db):
    conn = temp_db.get_connection()
    conn.execute("DELETE FROM settings WHERE key = 'review_prompt'")
    conn.execute("DELETE FROM schema_migrations WHERE name = ?", (GATE,))
    conn.commit()
    _run(temp_db)
    assert conn.execute("SELECT COUNT(*) FROM settings WHERE key = 'review_prompt'").fetchone()[0] == 0
