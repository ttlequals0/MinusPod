"""Shared database seeding and legacy-schema helpers for migration tests."""


def _seed(temp_db, slug='origin-test', episode_id='a1b2c3d4e5f6', transcript=None):
    """Create a podcast and one episode; returns (podcast_id, episode_id)."""
    temp_db.create_podcast(slug, 'https://example.com/feed.xml', 'Test Podcast')
    temp_db.upsert_episode(slug=slug, episode_id=episode_id,
                           original_url='https://example.com/ep.mp3',
                           title='Test Episode', original_duration=3600.0)
    if transcript:
        temp_db.save_episode_details(slug, episode_id, transcript_text=transcript)
    return temp_db.get_podcast_by_slug(slug)['id'], episode_id


def assert_column_migrated(db, table, column):
    """Drop a column, rerun the schema migrations, and assert it returns as NULL with other rows intact."""
    _seed(db)
    conn = db.get_connection()
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})") if r[1] != column]
    keep = ', '.join(cols)
    conn.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
    conn.commit()
    before = conn.execute(f"SELECT {keep} FROM {table}").fetchall()  # noqa: S608
    assert before

    db._run_schema_migrations()

    assert column in {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    assert conn.execute(f"SELECT {keep} FROM {table}").fetchall() == before  # noqa: S608
    assert {r[0] for r in conn.execute(f"SELECT {column} FROM {table}")} == {None}  # noqa: S608


def _rebuild_pre_migration_shape(conn):
    """Rebuild ad_patterns and pattern_corrections in the v2.1.x shape, with seeded sponsors cleared."""
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("DELETE FROM known_sponsors")
    conn.execute("DROP TABLE IF EXISTS ad_patterns")
    conn.execute("DROP TABLE IF EXISTS pattern_corrections")
    conn.execute("DROP TABLE IF EXISTS _migration_backup_ad_patterns_sponsor")
    conn.execute("""
        CREATE TABLE ad_patterns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scope TEXT NOT NULL CHECK(scope IN ('global', 'network', 'podcast')),
            network_id TEXT,
            podcast_id TEXT,
            dai_platform TEXT,
            text_template TEXT,
            intro_variants TEXT DEFAULT '[]',
            outro_variants TEXT DEFAULT '[]',
            sponsor TEXT,
            confirmation_count INTEGER DEFAULT 0,
            false_positive_count INTEGER DEFAULT 0,
            last_matched_at TEXT,
            created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
            created_from_episode_id TEXT,
            is_active INTEGER DEFAULT 1,
            disabled_at TEXT,
            disabled_reason TEXT,
            avg_duration REAL,
            duration_samples INTEGER DEFAULT 0
        )
    """)
    conn.execute("""
        CREATE TABLE pattern_corrections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pattern_id INTEGER,
            episode_id TEXT,
            podcast_title TEXT,
            episode_title TEXT,
            correction_type TEXT NOT NULL CHECK(correction_type IN (
                'false_positive', 'boundary_adjustment', 'confirm', 'promotion'
            )),
            original_bounds TEXT,
            corrected_bounds TEXT,
            text_snippet TEXT,
            created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
        )
    """)
    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")
