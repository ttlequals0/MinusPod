"""Migration test for the credited_time_saved backfill (#727).

Builds a DB with two episodes already credited under the pre-2.96.3 counter
(credited_time_saved column absent, so it lands as NULL) and asserts the
backfill sets each row's credit from its stored durations, resets
total_time_saved to their sum, and is a no-op on a second run.
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
    # Current episodes DDL minus credited_time_saved, matching a pre-2.96.3 DB.
    legacy_episodes_ddl = re.sub(
        r"\n\s*-- Lifetime time-saved counter dedup.*\n\s*-- to the total_time_saved.*"
        r"\n\s*credited_time_saved REAL,\n",
        "\n", TABLE_DDL['episodes']
    )
    assert 'credited_time_saved' not in legacy_episodes_ddl
    conn.execute(legacy_episodes_ddl)
    conn.execute(TABLE_DDL['episode_details'])
    conn.execute(TABLE_DDL['settings'])
    conn.execute(TABLE_DDL['stats'])
    conn.execute(
        "INSERT INTO podcasts (slug, source_url, title) VALUES (?, ?, ?)",
        ('migration-feed', 'https://example.com/feed.xml', 'Migration Feed'),
    )
    # Two episodes already credited under the old counter: original counter
    # already added their full saving to total_time_saved.
    conn.execute(
        "INSERT INTO episodes (podcast_id, episode_id, original_url, status, "
        "original_duration, new_duration) VALUES (1, 'ep-1', "
        "'https://example.com/ep1.mp3', 'processed', 3600.0, 3000.0)"
    )
    conn.execute(
        "INSERT INTO episodes (podcast_id, episode_id, original_url, status, "
        "original_duration, new_duration) VALUES (1, 'ep-2', "
        "'https://example.com/ep2.mp3', 'processed', 1800.0, 1500.0)"
    )
    conn.execute(
        "INSERT INTO stats (key, value) VALUES ('total_time_saved', 900.0)"
    )
    conn.commit()
    conn.close()
    return path


def test_backfill_credits_existing_rows_and_resets_total(legacy_db_path):
    from database import Database

    Database._instance = None
    db = Database(data_dir=str(legacy_db_path.parent))
    conn = db.get_connection()

    rows = {
        row['episode_id']: row['credited_time_saved']
        for row in conn.execute("SELECT episode_id, credited_time_saved FROM episodes")
    }
    assert rows['ep-1'] == pytest.approx(600.0)
    assert rows['ep-2'] == pytest.approx(300.0)

    total = conn.execute(
        "SELECT value FROM stats WHERE key = 'total_time_saved'"
    ).fetchone()['value']
    assert total == pytest.approx(900.0)

    gate = conn.execute(
        "SELECT 1 FROM schema_migrations WHERE name = 'backfill_credited_time_saved'"
    ).fetchone()
    assert gate is not None

    Database._instance = None


def test_backfill_is_a_no_op_on_second_run(legacy_db_path):
    from database import Database

    Database._instance = None
    db = Database(data_dir=str(legacy_db_path.parent))
    conn = db.get_connection()

    before = {
        row['episode_id']: row['credited_time_saved']
        for row in conn.execute("SELECT episode_id, credited_time_saved FROM episodes")
    }
    before_total = conn.execute(
        "SELECT value FROM stats WHERE key = 'total_time_saved'"
    ).fetchone()['value']

    db._run_backfill_credited_time_saved(conn)

    after = {
        row['episode_id']: row['credited_time_saved']
        for row in conn.execute("SELECT episode_id, credited_time_saved FROM episodes")
    }
    after_total = conn.execute(
        "SELECT value FROM stats WHERE key = 'total_time_saved'"
    ).fetchone()['value']

    assert after == before
    assert after_total == pytest.approx(before_total)

    Database._instance = None
