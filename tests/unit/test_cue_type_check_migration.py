"""Rebuild migration that drops the legacy cue_type CHECK on audio_cue_templates
so new cue types (#350 content_transition) are accepted on DBs created while the
CHECK still existed. Runs the real Database init path against a seeded legacy DB.
"""
import sqlite3

import pytest

from database import Database

# audio_cue_templates exactly as fresh installs created it while cue_type still
# carried the CHECK (the case the rebuild migration must fix).
OLD_CREATE = """
    CREATE TABLE audio_cue_templates (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        podcast_id INTEGER NOT NULL,
        label TEXT NOT NULL,
        source_episode_id TEXT,
        source_offset_s REAL NOT NULL,
        duration_s REAL NOT NULL,
        sample_rate INTEGER NOT NULL,
        n_coeffs INTEGER NOT NULL,
        mfcc_blob BLOB NOT NULL,
        pcm_blob BLOB,
        pcm_sample_rate INTEGER,
        scope TEXT NOT NULL DEFAULT 'podcast' CHECK(scope IN ('network', 'podcast')),
        network_id TEXT,
        cue_type TEXT NOT NULL DEFAULT 'ad_break_boundary' CHECK(cue_type IN ('ad_break_boundary', 'ad_break_start', 'ad_break_end', 'show_intro', 'show_outro')),
        enabled INTEGER NOT NULL DEFAULT 1,
        created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
        created_by TEXT DEFAULT 'user',
        FOREIGN KEY (podcast_id) REFERENCES podcasts(id) ON DELETE CASCADE
    )
"""

SEEDED_TYPES = ['ad_break_boundary', 'ad_break_start', 'ad_break_end',
                'show_intro', 'show_outro']

_INSERT = (
    "INSERT INTO audio_cue_templates "
    "(podcast_id, label, source_offset_s, duration_s, sample_rate, n_coeffs, mfcc_blob, cue_type) "
    "VALUES (1, ?, 0.0, 0.5, 16000, 13, ?, ?)"
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
    for i, ct in enumerate(SEEDED_TYPES, start=1):
        conn.execute(_INSERT, (f"tpl-{i}", b'\x00\x01', ct))
    conn.commit()
    conn.close()


@pytest.fixture(autouse=True)
def _reset_singleton():
    Database._instance = None
    yield
    Database._instance = None


def test_legacy_check_rejects_content_transition_before_migration(tmp_path):
    db_path = tmp_path / 'podcast.db'
    _seed_legacy_db(db_path)
    conn = sqlite3.connect(db_path)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(_INSERT, ('blocked', b'\x00', 'content_transition'))
    conn.close()


def test_rebuild_preserves_rows_and_accepts_content_transition(tmp_path):
    _seed_legacy_db(tmp_path / 'podcast.db')

    db = Database(data_dir=str(tmp_path))  # _init_schema runs the rebuild
    conn = db.get_connection()

    rows = conn.execute(
        "SELECT cue_type FROM audio_cue_templates ORDER BY id").fetchall()
    assert [r[0] for r in rows] == SEEDED_TYPES  # nothing lost

    sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE name='audio_cue_templates'"
    ).fetchone()[0]
    assert 'CHECK(cue_type' not in sql  # CHECK dropped

    # The new type now inserts (was IntegrityError before).
    conn.execute(_INSERT, ('ct', b'\x00', 'content_transition'))
    conn.commit()
    assert conn.execute(
        "SELECT COUNT(*) FROM audio_cue_templates WHERE cue_type='content_transition'"
    ).fetchone()[0] == 1

    names = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' "
        "AND tbl_name='audio_cue_templates'").fetchall()}
    assert {'idx_cue_templates_feed', 'idx_cue_templates_scope'} <= names


def test_migration_idempotent_on_second_init(tmp_path):
    _seed_legacy_db(tmp_path / 'podcast.db')

    Database._instance = None
    Database(data_dir=str(tmp_path)).get_connection().execute("SELECT 1")

    Database._instance = None
    conn = Database(data_dir=str(tmp_path)).get_connection()  # must not error/lose rows
    assert conn.execute("SELECT COUNT(*) FROM audio_cue_templates").fetchone()[0] == len(SEEDED_TYPES)
    sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE name='audio_cue_templates'"
    ).fetchone()[0]
    assert 'CHECK(cue_type' not in sql


def test_rebuild_preserves_threshold_and_existing_removal_preference(tmp_path):
    path = tmp_path / 'podcast.db'
    _seed_legacy_db(path)
    conn = sqlite3.connect(path)
    conn.execute('ALTER TABLE audio_cue_templates ADD COLUMN score_threshold REAL')
    conn.execute('ALTER TABLE audio_cue_templates ADD COLUMN remove_with_ad INTEGER NOT NULL DEFAULT 1')
    conn.execute('UPDATE audio_cue_templates SET score_threshold=0.87, remove_with_ad=0 WHERE id=1')
    conn.row_factory = sqlite3.Row
    before = [dict(row) for row in conn.execute('SELECT * FROM audio_cue_templates ORDER BY id')]
    conn.commit()
    conn.close()
    db = Database(data_dir=str(tmp_path))
    after = db.get_connection().execute('SELECT * FROM audio_cue_templates ORDER BY id').fetchall()
    assert [dict(row) for row in after] == before


def test_existing_templates_default_to_removal_without_data_loss(tmp_path):
    path = tmp_path / 'podcast.db'
    _seed_legacy_db(path)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    before = [dict(row) for row in conn.execute('SELECT * FROM audio_cue_templates ORDER BY id')]
    conn.close()
    db = Database(data_dir=str(tmp_path))
    after = [dict(row) for row in db.get_connection().execute('SELECT * FROM audio_cue_templates ORDER BY id')]
    for old, migrated in zip(before, after, strict=True):
        assert {key: migrated[key] for key in old} == old
        assert migrated['remove_with_ad'] == 1
