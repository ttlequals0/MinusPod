"""Schema upgrade path for audio_cue_templates (#350).

Simulates an existing database whose audio_cue_templates table predates the
pcm_blob / scope / network_id columns (e.g. created by an earlier build) and
asserts the guarded ALTERs add them without data loss.
"""
import sqlite3

from database import Database


def test_upgrade_adds_pcm_and_scope_columns(tmp_path):
    Database._instance = None
    db_path = tmp_path / 'podcast.db'
    conn = sqlite3.connect(str(db_path))
    # Old-shape table: the original branch's columns, missing pcm/scope/network.
    conn.executescript("""
        CREATE TABLE podcasts (id INTEGER PRIMARY KEY, slug TEXT);
        INSERT INTO podcasts (id, slug) VALUES (1, 'old-show');
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
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT,
            created_by TEXT DEFAULT 'user'
        );
        INSERT INTO audio_cue_templates
            (podcast_id, label, source_offset_s, duration_s, sample_rate, n_coeffs, mfcc_blob)
            VALUES (1, 'legacy-cue', 1.0, 0.5, 16000, 13, X'00');
    """)
    conn.commit()
    conn.close()

    db = Database(data_dir=str(tmp_path))
    try:
        conn = db.get_connection()
        cols = {r['name'] for r in conn.execute("PRAGMA table_info(audio_cue_templates)").fetchall()}
        # New columns added by the guarded ALTERs.
        assert {'pcm_blob', 'pcm_sample_rate', 'scope', 'network_id'} <= cols
        # The pre-existing row survived (no data loss) and defaults to podcast scope.
        row = conn.execute(
            "SELECT label, scope FROM audio_cue_templates WHERE label = 'legacy-cue'"
        ).fetchone()
        assert row is not None
        assert row['scope'] == 'podcast'
    finally:
        Database._instance = None
