"""Tests for the v2.2.0 sponsor FK migration in SchemaMixin._migrate_sponsor_fk."""
import pytest


# --- Helpers -------------------------------------------------------------

def _rebuild_pre_migration_shape(conn):
    """Rebuild `ad_patterns` and `pattern_corrections` in the v2.1.x shape so
    we can exercise the migration end-to-end. Assumes the post-migration
    tables have just been created by the normal Database init.

    The v2.4.0 seed migration preloads 255 sponsors; we clear them here so
    tests can stage their own sponsor case-variants without colliding on the
    UNIQUE name constraint.
    """
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


def _column_names(conn, table):
    return {r['name'] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _check_constraint(conn, table):
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
        (table,)
    ).fetchone()
    return row['sql'] if row else ''


# --- End-to-end happy path -----------------------------------------------

def test_migration_happy_path(temp_db):
    conn = temp_db.get_connection()
    _rebuild_pre_migration_shape(conn)

    conn.execute(
        "INSERT INTO ad_patterns (scope, text_template, sponsor) VALUES ('global', 'ad1 text', 'Squarespace')"
    )
    conn.execute(
        "INSERT INTO ad_patterns (scope, text_template, sponsor) VALUES ('global', 'ad2 text', 'BetterHelp')"
    )
    conn.execute(
        "INSERT INTO ad_patterns (scope, text_template, sponsor) VALUES ('global', 'ad3 text', NULL)"
    )
    conn.execute(
        "INSERT INTO pattern_corrections (pattern_id, correction_type) VALUES (1, 'confirm')"
    )
    conn.execute(
        "INSERT INTO pattern_corrections (pattern_id, correction_type) VALUES (2, 'boundary_adjustment')"
    )
    conn.commit()

    temp_db._migrate_sponsor_fk(conn)

    # Schema post-migration
    ap_cols = _column_names(conn, 'ad_patterns')
    assert 'sponsor' not in ap_cols
    assert 'sponsor_id' in ap_cols
    assert 'created_by' in ap_cols
    pc_cols = _column_names(conn, 'pattern_corrections')
    assert 'sponsor_id' in pc_cols

    # Data preserved
    rows = conn.execute(
        "SELECT id, text_template, sponsor_id, created_by FROM ad_patterns ORDER BY id"
    ).fetchall()
    assert len(rows) == 3
    assert rows[0]['text_template'] == 'ad1 text'
    assert rows[0]['sponsor_id'] is not None
    assert rows[0]['created_by'] == 'auto'
    assert rows[1]['sponsor_id'] is not None
    assert rows[2]['sponsor_id'] is None  # row had NULL sponsor

    # Sponsor names round-trip
    by_id = {
        r['id']: r['name']
        for r in conn.execute(
            "SELECT id, name FROM known_sponsors"
        ).fetchall()
    }
    assert by_id[rows[0]['sponsor_id']] == 'Squarespace'
    assert by_id[rows[1]['sponsor_id']] == 'BetterHelp'

    # pattern_corrections.sponsor_id back-filled from joined ad_pattern row
    pcs = conn.execute(
        "SELECT pattern_id, sponsor_id FROM pattern_corrections ORDER BY id"
    ).fetchall()
    assert pcs[0]['sponsor_id'] == rows[0]['sponsor_id']
    assert pcs[1]['sponsor_id'] == rows[1]['sponsor_id']

    # CHECK constraint extended
    assert "'auto_promotion'" in _check_constraint(conn, 'pattern_corrections')
    assert "'create'" in _check_constraint(conn, 'pattern_corrections')

    # Backup table is gone
    assert conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        ('_migration_backup_ad_patterns_sponsor',)
    ).fetchone() is None

    # FK check is clean
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


# --- Idempotency ---------------------------------------------------------

def test_migration_is_idempotent(temp_db):
    conn = temp_db.get_connection()
    _rebuild_pre_migration_shape(conn)
    conn.execute(
        "INSERT INTO ad_patterns (scope, text_template, sponsor) VALUES ('global', 'ad1', 'Squarespace')"
    )
    conn.commit()

    temp_db._migrate_sponsor_fk(conn)
    sponsor_id_first = conn.execute(
        "SELECT sponsor_id FROM ad_patterns WHERE id = 1"
    ).fetchone()['sponsor_id']

    # Run again on now-migrated schema; should be a no-op
    temp_db._migrate_sponsor_fk(conn)

    sponsor_id_second = conn.execute(
        "SELECT sponsor_id FROM ad_patterns WHERE id = 1"
    ).fetchone()['sponsor_id']
    assert sponsor_id_first == sponsor_id_second

    # known_sponsors should still have exactly one Squarespace row
    rows = conn.execute(
        "SELECT * FROM known_sponsors WHERE LOWER(name) = 'squarespace'"
    ).fetchall()
    assert len(rows) == 1


def test_migration_on_fresh_post_schema_is_noop(temp_db):
    """A fresh Database init already creates new-shape tables. Running the
    migration again must be a clean no-op (no extra rows, no errors)."""
    conn = temp_db.get_connection()
    sponsors_before = conn.execute(
        "SELECT COUNT(*) AS n FROM known_sponsors"
    ).fetchone()['n']
    temp_db._migrate_sponsor_fk(conn)
    sponsors_after = conn.execute(
        "SELECT COUNT(*) AS n FROM known_sponsors"
    ).fetchone()['n']
    assert sponsors_before == sponsors_after


# --- Case-variant dedup ---------------------------------------------------

def test_migration_dedupes_case_variants_in_known_sponsors(temp_db):
    conn = temp_db.get_connection()
    _rebuild_pre_migration_shape(conn)
    # Three case variants of the same sponsor pre-exist in known_sponsors;
    # lowest id wins after dedup.
    conn.execute("INSERT INTO known_sponsors (name) VALUES ('Squarespace')")
    conn.execute("INSERT INTO known_sponsors (name) VALUES ('squarespace')")
    conn.execute("INSERT INTO known_sponsors (name) VALUES ('SQUARESPACE')")
    keep_id = conn.execute(
        "SELECT id FROM known_sponsors WHERE name = 'Squarespace'"
    ).fetchone()['id']

    conn.execute(
        "INSERT INTO ad_patterns (scope, text_template, sponsor) VALUES ('global', 'ad1', 'Squarespace')"
    )
    conn.execute(
        "INSERT INTO ad_patterns (scope, text_template, sponsor) VALUES ('global', 'ad2', 'squarespace')"
    )
    conn.commit()

    temp_db._migrate_sponsor_fk(conn)

    sponsor_rows = conn.execute(
        "SELECT id, name FROM known_sponsors"
    ).fetchall()
    matching = [r for r in sponsor_rows if r['name'].lower() == 'squarespace']
    assert len(matching) == 1
    assert matching[0]['id'] == keep_id

    sponsor_ids = {
        r['sponsor_id']
        for r in conn.execute("SELECT sponsor_id FROM ad_patterns").fetchall()
    }
    assert sponsor_ids == {keep_id}


# --- Verification gate ----------------------------------------------------

def test_migration_skips_destructive_on_unresolvable_sponsor(temp_db):
    """If a sponsor name sanitizes to None, that row keeps sponsor_id NULL,
    parity check fails, and the destructive drop is skipped. The old
    `sponsor` column stays in place so the user can recover."""
    conn = temp_db.get_connection()
    _rebuild_pre_migration_shape(conn)
    conn.execute(
        "INSERT INTO ad_patterns (scope, text_template, sponsor) VALUES ('global', 'ad1', 'Squarespace')"
    )
    # A control-char-only sponsor name sanitizes to None.
    conn.execute(
        "INSERT INTO ad_patterns (scope, text_template, sponsor) VALUES ('global', 'ad2', ?)",
        ('\x00\x07',)
    )
    conn.commit()

    temp_db._migrate_sponsor_fk(conn)

    # Old text column must still be present because destructive steps were skipped
    ap_cols = _column_names(conn, 'ad_patterns')
    assert 'sponsor' in ap_cols
    assert 'sponsor_id' in ap_cols  # the additive step happened

    # Backup table still around so a retry can compare counts
    assert conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        ('_migration_backup_ad_patterns_sponsor',)
    ).fetchone() is not None


# --- pattern_corrections CHECK accepts the new values --------------------

def test_create_correction_type_accepted_after_migration(temp_db):
    """`create` and `auto_promotion` must both be INSERT-able after migration."""
    conn = temp_db.get_connection()
    _rebuild_pre_migration_shape(conn)
    conn.execute(
        "INSERT INTO ad_patterns (scope, text_template, sponsor) VALUES ('global', 'ad1', 'Squarespace')"
    )
    conn.commit()
    temp_db._migrate_sponsor_fk(conn)

    # Both new types insert without violating the CHECK constraint
    conn.execute(
        "INSERT INTO pattern_corrections (pattern_id, correction_type) VALUES (1, 'create')"
    )
    conn.execute(
        "INSERT INTO pattern_corrections (pattern_id, correction_type) VALUES (1, 'auto_promotion')"
    )
    conn.commit()

    types = {
        r['correction_type']
        for r in conn.execute("SELECT correction_type FROM pattern_corrections").fetchall()
    }
    assert 'create' in types
    assert 'auto_promotion' in types
