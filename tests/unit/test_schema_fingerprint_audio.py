"""Schema for per-pattern audio state and the fingerprint match history table."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from database import Database  # noqa: E402

# audio_fingerprints as created before the audio-state columns existed.
OLD_AUDIO_FINGERPRINTS_DDL = """CREATE TABLE audio_fingerprints (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern_id INTEGER UNIQUE REFERENCES ad_patterns(id) ON DELETE CASCADE,
    fingerprint BLOB,
    duration REAL,
    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
)"""

NEW_FP_COLUMNS = ('enabled', 'source_podcast_slug', 'source_episode_id',
                  'source_start', 'source_end')


@pytest.fixture
def db_path(tmp_path):
    Database._instance = None
    yield str(tmp_path / 'test.db')
    Database._instance = None


def _open(path):
    Database._instance = None
    return Database(path)


def _shape(conn):
    tables = {}
    for table in ('audio_fingerprints', 'audio_fingerprint_matches', 'ad_patterns'):
        tables[table] = [tuple(r) for r in conn.execute(f"PRAGMA table_info({table})")]
    indexes = sorted(
        (r['name'], r['tbl_name'],
         tuple(c['name'] for c in conn.execute(f"PRAGMA index_info({r['name']})")))
        for r in conn.execute(
            "SELECT name, tbl_name FROM sqlite_master WHERE type = 'index' "
            "AND name NOT LIKE 'sqlite_%'"))
    fks = {t: sorted(tuple(r)[2:] for r in conn.execute(f"PRAGMA foreign_key_list({t})"))
           for t in tables}
    uniques = {t: sorted(
        (r['name'], tuple(c['name'] for c in conn.execute(f"PRAGMA index_info({r['name']})")))
        for r in conn.execute(f"PRAGMA index_list({t})") if r['unique'])
        for t in tables}
    return tables, indexes, fks, uniques


def _downgrade(conn):
    """Return a migrated database to the shape before the audio-state change."""
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("DROP TABLE audio_fingerprint_matches")
    conn.execute("DROP TABLE audio_fingerprints")
    conn.execute(OLD_AUDIO_FINGERPRINTS_DDL)
    conn.execute("CREATE INDEX idx_fingerprints_pattern ON audio_fingerprints(pattern_id)")
    conn.execute("ALTER TABLE ad_patterns DROP COLUMN audio_removed")
    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")


def _add_pattern(conn):
    pid = conn.execute(
        "INSERT INTO ad_patterns (scope, podcast_id, text_template) "
        "VALUES ('podcast', 'slug', 'buy now')").lastrowid
    conn.commit()
    return pid


def test_fresh_db_has_audio_columns_and_match_table(db_path):
    conn = _open(db_path).get_connection()
    fp = {r['name'] for r in conn.execute("PRAGMA table_info(audio_fingerprints)")}
    assert set(NEW_FP_COLUMNS) <= fp
    ap = {r['name'] for r in conn.execute("PRAGMA table_info(ad_patterns)")}
    assert 'audio_removed' in ap
    cols = {r['name']: r for r in conn.execute(
        "PRAGMA table_info(audio_fingerprint_matches)")}
    assert {'pattern_id', 'episode_id', 'podcast_slug', 'match_start', 'match_end',
            'similarity', 'coverage', 'in_scope', 'audio_only', 'rejected',
            'rejected_at', 'created_at'} <= set(cols)
    assert cols['podcast_slug']['notnull'] == 1
    assert cols['episode_id']['notnull'] == 1


def test_fresh_db_has_match_indexes(db_path):
    conn = _open(db_path).get_connection()
    idx = {r['name']: [c['name'] for c in conn.execute(
        f"PRAGMA index_info({r['name']})")] for r in conn.execute(
        "PRAGMA index_list(audio_fingerprint_matches)")}
    assert idx['idx_fp_matches_pattern'] == ['pattern_id', 'created_at']
    assert idx['idx_fp_matches_episode'] == ['podcast_slug', 'episode_id']


def test_migrated_db_matches_fresh_db_shape(tmp_path):
    fresh = _shape(_open(str(tmp_path / 'fresh.db')).get_connection())

    old_path = str(tmp_path / 'old.db')
    _downgrade(_open(old_path).get_connection())
    old_conn = _open(old_path).get_connection()
    assert _shape(old_conn) == fresh


def test_migration_keeps_existing_fingerprints(db_path):
    conn = _open(db_path).get_connection()
    pid = _add_pattern(conn)
    _downgrade(conn)
    conn.execute("INSERT INTO audio_fingerprints (pattern_id, fingerprint, duration) "
                 "VALUES (?, ?, 12.5)", (pid, b'keep'))
    conn.commit()

    row = _open(db_path).get_connection().execute(
        "SELECT * FROM audio_fingerprints WHERE pattern_id = ?", (pid,)).fetchone()
    assert row['fingerprint'] == b'keep' and row['duration'] == 12.5
    assert row['enabled'] == 1
    assert row['source_podcast_slug'] is None and row['source_end'] is None


def test_second_open_changes_nothing(db_path):
    conn = _open(db_path).get_connection()
    _downgrade(conn)
    first = _open(db_path).get_connection()
    shape = _shape(first)
    second = _open(db_path).get_connection()
    assert _shape(second) == shape
    assert second.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == \
        first.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]


def test_cascade_rebuild_carries_audio_columns(db_path):
    db = _open(db_path)
    conn = db.get_connection()
    pid = _add_pattern(conn)
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("DROP TABLE audio_fingerprints")
    conn.execute("""CREATE TABLE audio_fingerprints (
        id INTEGER PRIMARY KEY AUTOINCREMENT, pattern_id INTEGER UNIQUE,
        fingerprint BLOB, duration REAL,
        created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
        enabled INTEGER NOT NULL DEFAULT 1, source_podcast_slug TEXT,
        source_episode_id TEXT, source_start REAL, source_end REAL)""")
    conn.execute(
        "INSERT INTO audio_fingerprints (pattern_id, fingerprint, duration, enabled, "
        "source_podcast_slug, source_episode_id, source_start, source_end) "
        "VALUES (?, ?, 9.0, 0, 'show', 'ep1', 3.5, 12.5)", (pid, b'fp'))
    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")

    db._migrate_fingerprint_cascade(conn)

    row = conn.execute("SELECT * FROM audio_fingerprints").fetchone()
    assert (row['enabled'], row['source_podcast_slug'], row['source_episode_id'],
            row['source_start'], row['source_end']) == (0, 'show', 'ep1', 3.5, 12.5)


def test_foreign_keys_on_and_pattern_delete_cascades_matches(db_path):
    db = _open(db_path)
    conn = db.get_connection()
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    pid = _add_pattern(conn)
    conn.execute(
        "INSERT INTO audio_fingerprint_matches "
        "(pattern_id, episode_id, podcast_slug, match_start, match_end) "
        "VALUES (?, 'ep1', 'show', 1.0, 2.0)", (pid,))
    conn.commit()
    db.delete_ad_pattern(pid)
    assert conn.execute(
        "SELECT COUNT(*) FROM audio_fingerprint_matches").fetchone()[0] == 0


def test_cascade_rebuild_archives_orphans_with_audio_columns(db_path):
    db = _open(db_path)
    conn = db.get_connection()
    pid = _add_pattern(conn)
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("DROP TABLE audio_fingerprints")
    conn.execute("""CREATE TABLE audio_fingerprints (
        id INTEGER PRIMARY KEY AUTOINCREMENT, pattern_id INTEGER UNIQUE,
        fingerprint BLOB, duration REAL,
        created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
        enabled INTEGER NOT NULL DEFAULT 1, source_podcast_slug TEXT,
        source_episode_id TEXT, source_start REAL, source_end REAL)""")
    conn.execute(
        "INSERT INTO audio_fingerprints (pattern_id, fingerprint, duration, enabled, "
        "source_podcast_slug, source_episode_id, source_start, source_end) "
        "VALUES (?, ?, 9.0, 0, 'show', 'ep1', 3.5, 12.5)", (pid + 500, b'orphan'))
    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")

    db._migrate_fingerprint_cascade(conn)

    row = conn.execute("SELECT * FROM _orphaned_audio_fingerprints").fetchone()
    assert (row['pattern_id'], row['fingerprint'], row['enabled'], row['source_podcast_slug'],
            row['source_episode_id'], row['source_start'], row['source_end']) == \
        (pid + 500, b'orphan', 0, 'show', 'ep1', 3.5, 12.5)
    assert conn.execute("SELECT COUNT(*) FROM audio_fingerprints").fetchone()[0] == 0


def test_cascade_rebuild_archives_orphans_of_the_old_shape(db_path):
    db = _open(db_path)
    conn = db.get_connection()
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("DROP TABLE audio_fingerprints")
    conn.execute(OLD_AUDIO_FINGERPRINTS_DDL.replace("REFERENCES ad_patterns(id) ON DELETE CASCADE", ""))
    conn.execute("INSERT INTO audio_fingerprints (pattern_id, fingerprint, duration) "
                 "VALUES (999, ?, 9.0)", (b'orphan',))
    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")

    db._migrate_fingerprint_cascade(conn)

    row = conn.execute("SELECT * FROM _orphaned_audio_fingerprints").fetchone()
    assert row['pattern_id'] == 999 and row['fingerprint'] == b'orphan'
    assert 'enabled' not in row.keys()
