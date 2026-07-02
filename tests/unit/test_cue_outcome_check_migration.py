"""Rebuild migration that drops the legacy outcome CHECK on cue_detections so the
new 'below_threshold' near-miss outcome (#350 Phase 6) is accepted on DBs created
while the CHECK still listed only ('snap', 'pair', 'none'). Runs the real
Database init path against a seeded legacy DB. THE DATA-LOSS RULE IS ABSOLUTE:
every seeded row must survive the rebuild.
"""
import sqlite3

import pytest

from database import Database

# cue_detections exactly as fresh installs created it while outcome still carried
# the CHECK that predates 'below_threshold' (the case the rebuild must fix).
OLD_CREATE = """
    CREATE TABLE cue_detections (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        podcast_id INTEGER NOT NULL,
        episode_id TEXT NOT NULL,
        template_id INTEGER,
        label TEXT,
        cue_type TEXT,
        role TEXT,
        source TEXT NOT NULL DEFAULT 'template',
        start_s REAL NOT NULL,
        end_s REAL NOT NULL,
        match_score REAL,
        confidence REAL,
        outcome TEXT NOT NULL DEFAULT 'none' CHECK(outcome IN ('snap', 'pair', 'none')),
        verdict TEXT NOT NULL DEFAULT 'pending' CHECK(verdict IN ('pending', 'confirmed', 'rejected')),
        created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
        FOREIGN KEY (podcast_id) REFERENCES podcasts(id) ON DELETE CASCADE
    )
"""

# One row per legacy outcome so the rebuild is proven to preserve every value.
SEEDED_OUTCOMES = ['snap', 'pair', 'none']

_INSERT = (
    "INSERT INTO cue_detections "
    "(podcast_id, episode_id, template_id, label, start_s, end_s, match_score, confidence, outcome) "
    "VALUES (1, ?, 1, ?, ?, ?, ?, ?, ?)"
)


def _seed_legacy_db(path):
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE podcasts (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "slug TEXT UNIQUE NOT NULL, source_url TEXT NOT NULL, title TEXT)"
    )
    conn.execute("INSERT INTO podcasts (id, slug, source_url, title) "
                 "VALUES (1, 'feed', 'https://example.com/a.xml', 'Feed')")
    conn.execute(OLD_CREATE)
    for i, outcome in enumerate(SEEDED_OUTCOMES, start=1):
        conn.execute(
            _INSERT,
            (f"ep-{i}", f"tpl-{i}", float(i), float(i) + 0.5, 0.8, 0.9, outcome),
        )
    conn.commit()
    conn.close()


@pytest.fixture(autouse=True)
def _reset_singleton():
    Database._instance = None
    yield
    Database._instance = None


def test_legacy_check_rejects_below_threshold_before_migration(tmp_path):
    db_path = tmp_path / 'podcast.db'
    _seed_legacy_db(db_path)
    conn = sqlite3.connect(db_path)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            _INSERT, ('blocked', 'tpl-x', 9.0, 9.5, 0.7, 0.7, 'below_threshold'))
    conn.close()


def test_rebuild_preserves_rows_and_accepts_below_threshold(tmp_path):
    _seed_legacy_db(tmp_path / 'podcast.db')

    db = Database(data_dir=str(tmp_path))  # _init_schema runs the rebuild
    conn = db.get_connection()

    # Row-count survives: every seeded row is still present.
    assert conn.execute(
        "SELECT COUNT(*) FROM cue_detections").fetchone()[0] == len(SEEDED_OUTCOMES)
    outcomes = conn.execute(
        "SELECT outcome FROM cue_detections ORDER BY id").fetchall()
    assert [r[0] for r in outcomes] == SEEDED_OUTCOMES  # nothing lost

    sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE name='cue_detections'"
    ).fetchone()[0]
    assert 'CHECK(outcome' not in sql  # outcome CHECK dropped
    assert "CHECK(verdict" in sql       # verdict CHECK kept

    # The new outcome now inserts (was IntegrityError before).
    conn.execute(_INSERT, ('nm', 'tpl-nm', 20.0, 20.5, 0.7, 0.7, 'below_threshold'))
    conn.commit()
    assert conn.execute(
        "SELECT COUNT(*) FROM cue_detections WHERE outcome='below_threshold'"
    ).fetchone()[0] == 1

    # The three indexes are recreated after the rebuild.
    names = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' "
        "AND tbl_name='cue_detections'").fetchall()}
    assert {'idx_cue_detections_episode', 'idx_cue_detections_feed',
            'idx_cue_detections_template'} <= names

    # New nullable diagnostic columns exist after migration.
    cols = {r[1] for r in conn.execute("PRAGMA table_info(cue_detections)").fetchall()}
    assert 'edge_distance_s' in cols
    assert 'unused_reason' in cols


def test_migration_idempotent_on_second_init(tmp_path):
    _seed_legacy_db(tmp_path / 'podcast.db')

    Database._instance = None
    Database(data_dir=str(tmp_path)).get_connection().execute("SELECT 1")

    Database._instance = None
    conn = Database(data_dir=str(tmp_path)).get_connection()  # must not error/lose rows
    assert conn.execute(
        "SELECT COUNT(*) FROM cue_detections").fetchone()[0] == len(SEEDED_OUTCOMES)
    sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE name='cue_detections'"
    ).fetchone()[0]
    assert 'CHECK(outcome' not in sql


def test_migration_self_heals_orphan_rebuild_table(tmp_path):
    # A prior boot crashed mid-rebuild, leaving an orphan cue_detections_rebuild
    # and the legacy CHECK still on cue_detections. The migration must drop the
    # orphan, complete the rebuild, and accept below_threshold afterwards.
    db_path = tmp_path / 'podcast.db'
    _seed_legacy_db(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE cue_detections_rebuild (id INTEGER PRIMARY KEY, junk TEXT)")
    conn.execute("INSERT INTO cue_detections_rebuild (id, junk) VALUES (1, 'stale')")
    conn.commit()
    conn.close()

    Database._instance = None
    conn = Database(data_dir=str(tmp_path)).get_connection()  # must self-heal
    sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE name='cue_detections'"
    ).fetchone()[0]
    assert 'CHECK(outcome' not in sql
    assert conn.execute(
        "SELECT COUNT(*) FROM cue_detections").fetchone()[0] == len(SEEDED_OUTCOMES)
    conn.execute(_INSERT, ('nm', 'tpl-nm', 20.0, 20.5, 0.7, 0.7, 'below_threshold'))
    conn.commit()
    assert conn.execute(
        "SELECT COUNT(*) FROM cue_detections WHERE outcome='below_threshold'"
    ).fetchone()[0] == 1
