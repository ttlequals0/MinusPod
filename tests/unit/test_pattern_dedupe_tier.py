"""deduplicate_patterns must keep a user/community pattern over an auto one."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from database import Database  # noqa: E402

SLUG = 'dedupe-tier-test'


@pytest.fixture
def db(tmp_path):
    # Database is a singleton, so each test needs a fresh one.
    Database._instance = None
    yield Database(str(tmp_path / 'test.db'))
    Database._instance = None


def _add_pattern(db, created_by, confirmation_count, source='local'):
    conn = db.get_connection()
    pattern_id = conn.execute(
        "INSERT INTO ad_patterns (scope, podcast_id, text_template, created_by, "
        "source, confirmation_count) VALUES ('podcast', ?, 'buy now today', ?, ?, ?)",
        (SLUG, created_by, source, confirmation_count),
    ).lastrowid
    conn.commit()
    return pattern_id


def test_user_pattern_survives_a_more_confirmed_auto_pattern(db):
    user_id = _add_pattern(db, 'user', 10)
    auto_id = _add_pattern(db, 'auto', 17)

    removed = db.deduplicate_patterns()

    assert removed == 1
    conn = db.get_connection()
    remaining = conn.execute("SELECT id, confirmation_count FROM ad_patterns "
                              "WHERE podcast_id = ?", (SLUG,)).fetchall()
    assert len(remaining) == 1
    assert remaining[0]['id'] == user_id
    assert remaining[0]['confirmation_count'] >= 17
    assert conn.execute("SELECT COUNT(*) FROM ad_patterns WHERE id = ?",
                         (auto_id,)).fetchone()[0] == 0


def test_confirmation_count_still_breaks_ties_within_a_tier(db):
    low_id = _add_pattern(db, 'auto', 5)
    high_id = _add_pattern(db, 'auto', 9)

    removed = db.deduplicate_patterns()

    assert removed == 1
    conn = db.get_connection()
    remaining = conn.execute("SELECT id FROM ad_patterns WHERE podcast_id = ?",
                              (SLUG,)).fetchall()
    assert len(remaining) == 1
    assert remaining[0]['id'] == high_id
    assert conn.execute("SELECT COUNT(*) FROM ad_patterns WHERE id = ?",
                         (low_id,)).fetchone()[0] == 0
