"""Default prompt rows holding the old timestamp/curly markup are refreshed; customized rows are not."""
import pytest

import database
from database import Database
from database.schema import _prompt_has_legacy_markup

OLD_TEXT = "Example [45.0s - 48.0s] output {{\"start\": 1}}"
KEYS = {
    'system_prompt': database.DEFAULT_SYSTEM_PROMPT,
    'verification_prompt': database.DEFAULT_VERIFICATION_PROMPT,
    'review_prompt': database.DEFAULT_REVIEW_PROMPT,
    'resurrect_prompt': database.DEFAULT_RESURRECT_PROMPT,
}


@pytest.fixture
def conn(tmp_path):
    Database._instance = None
    db = Database(data_dir=str(tmp_path))
    c = db.get_connection()
    yield db, c
    Database._instance = None


def _seed(c, value, is_default):
    for key in KEYS:
        c.execute("INSERT OR REPLACE INTO settings (key, value, is_default) VALUES (?, ?, ?)",
                  (key, value, is_default))
    c.commit()


def _value(c, key):
    return c.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()[0]


def test_default_rows_with_old_markup_are_replaced(conn):
    db, c = conn
    _seed(c, OLD_TEXT, 1)
    db._run_schema_migrations()
    for key, new in KEYS.items():
        assert _value(c, key) == new


def test_customized_rows_untouched(conn):
    db, c = conn
    _seed(c, OLD_TEXT, 0)
    db._run_schema_migrations()
    for key in KEYS:
        assert _value(c, key) == OLD_TEXT


def test_default_rows_with_new_text_untouched(conn):
    db, c = conn
    for key, new in KEYS.items():
        c.execute("INSERT OR REPLACE INTO settings (key, value, is_default) VALUES (?, ?, 1)",
                  (key, new))
    c.commit()
    db._run_schema_migrations()
    for key, new in KEYS.items():
        assert _value(c, key) == new


@pytest.mark.parametrize("text,expected", [
    ("x {{ y", True),
    ("[12.3s - 14.0s]", True),
    ("[12.3s-14.0s]", True),
    ("[12.3-14.0]", False),
    ("a { b }", False),
    ("", False),
    (None, False),
])
def test_prompt_has_legacy_markup(text, expected):
    assert _prompt_has_legacy_markup(text) is expected
