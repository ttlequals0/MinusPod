"""Tests for the prompt placeholder substitution refactor."""
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from ad_detector import get_static_system_prompt
from utils.prompt import (
    SPONSOR_DATABASE_HEADER,
    format_sponsor_block,
    render_prompt,
)
from database import (
    DEFAULT_RESURRECT_PROMPT,
    DEFAULT_REVIEW_PROMPT,
    DEFAULT_SYSTEM_PROMPT,
    DEFAULT_VERIFICATION_PROMPT,
)


def test_render_prompt_substitutes_single_brace_placeholders():
    out = render_prompt('Hello {name}', name='world')
    assert out == 'Hello world'


def test_render_prompt_preserves_literal_double_braces():
    """Double-braced JSON examples in default prompts must not be touched."""
    out = render_prompt('JSON: {{"start": 1.0}} sub={x}', x='Y')
    assert out == 'JSON: {{"start": 1.0}} sub=Y'


def test_render_prompt_drops_missing_placeholders_silently():
    """Variables without a corresponding placeholder are no-ops (the user
    intentionally edited the placeholder out)."""
    out = render_prompt('Hello {a}', a='X', b='IGNORED')
    assert out == 'Hello X'


def test_render_prompt_absent_placeholder_means_no_insertion():
    """Key behavior change vs legacy append: no placeholder = no content."""
    out = render_prompt('Just plain text', sponsor_database='SHOULD NOT APPEAR')
    assert out == 'Just plain text'


def test_format_sponsor_block_empty_returns_empty():
    assert format_sponsor_block('') == ''


def test_format_sponsor_block_wraps_with_header():
    block = format_sponsor_block('AG1, BetterHelp')
    assert block.startswith(SPONSOR_DATABASE_HEADER)
    assert 'AG1, BetterHelp' in block


def test_default_system_prompt_has_sponsor_placeholder():
    assert '{sponsor_database}' in DEFAULT_SYSTEM_PROMPT


def test_default_verification_prompt_has_sponsor_placeholder():
    assert '{sponsor_database}' in DEFAULT_VERIFICATION_PROMPT


def test_default_review_prompt_has_both_placeholders():
    assert '{sponsor_database}' in DEFAULT_REVIEW_PROMPT
    assert '{max_boundary_shift_seconds}' in DEFAULT_REVIEW_PROMPT


def test_default_resurrect_prompt_has_sponsor_placeholder():
    assert '{sponsor_database}' in DEFAULT_RESURRECT_PROMPT


def test_get_static_system_prompt_substitutes_seed_sponsors():
    out = get_static_system_prompt()
    assert '{sponsor_database}' not in out  # placeholder consumed
    assert 'DYNAMIC SPONSOR DATABASE' in out  # block was inserted
    # Seed sponsors include a few well-known ones
    assert 'BetterHelp' in out or 'Athletic Greens' in out


def test_render_with_sponsors_empty_list_drops_block():
    """When the dynamic sponsor list is empty, no header should appear."""
    from ad_detector import AdDetector
    detector = AdDetector.__new__(AdDetector)
    # Bypass the sponsor_service property by stubbing the helper directly
    detector._get_sponsor_list_safely = lambda: ''
    out = detector._render_with_sponsors('Body{sponsor_database}END')
    assert out == 'BodyEND'


def test_render_with_sponsors_non_empty_list_inserts_block():
    from ad_detector import AdDetector
    detector = AdDetector.__new__(AdDetector)
    detector._get_sponsor_list_safely = lambda: 'AG1, Squarespace'
    out = detector._render_with_sponsors('Body{sponsor_database}END')
    assert 'DYNAMIC SPONSOR DATABASE' in out
    assert 'AG1, Squarespace' in out
    assert 'Body' in out and 'END' in out


# ---------- Migration tests ----------

def test_migration_appends_placeholder_to_customized_prompts(tmp_path):
    """Existing user-customized prompts get {sponsor_database} appended."""
    from database import Database
    Database._instance = None

    # Pre-create a DB with customized prompts that lack the placeholder
    db_path = tmp_path / "podcast.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE settings (
            key TEXT PRIMARY KEY,
            value TEXT,
            is_default INTEGER DEFAULT 0,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE podcasts (id INTEGER PRIMARY KEY, slug TEXT);
        INSERT INTO podcasts(slug) VALUES ('seed-pod');
        INSERT INTO settings (key, value, is_default)
            VALUES ('system_prompt', 'Custom system without placeholder', 0);
        INSERT INTO settings (key, value, is_default)
            VALUES ('verification_prompt', 'Custom verification without placeholder', 0);
    """)
    conn.commit()
    conn.close()

    db = Database(data_dir=str(tmp_path))

    sp = db.get_setting('system_prompt')
    vp = db.get_setting('verification_prompt')
    assert sp.endswith('{sponsor_database}')
    assert vp.endswith('{sponsor_database}')
    assert db.get_setting('_review_prompt_migrated') == 'true'


def test_migration_is_idempotent(tmp_path):
    """Running the migration twice does not double-append the placeholder."""
    from database import Database
    Database._instance = None

    db_path = tmp_path / "podcast.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE settings (
            key TEXT PRIMARY KEY,
            value TEXT,
            is_default INTEGER DEFAULT 0,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE podcasts (id INTEGER PRIMARY KEY, slug TEXT);
        INSERT INTO podcasts(slug) VALUES ('seed-pod');
        INSERT INTO settings (key, value, is_default)
            VALUES ('system_prompt', 'Custom no placeholder', 0);
    """)
    conn.commit()
    conn.close()

    db = Database(data_dir=str(tmp_path))
    first = db.get_setting('system_prompt')

    Database._instance = None
    db = Database(data_dir=str(tmp_path))
    second = db.get_setting('system_prompt')

    assert first == second
    # Exactly one placeholder, not two
    assert first.count('{sponsor_database}') == 1


def test_migration_skips_default_prompts(tmp_path):
    """is_default=1 rows are owned by the seed/refresh path; the placeholder
    backfill must leave them alone. Use a fixture value that pre-existing
    prompt-refresh migrations also skip (contains the keywords those
    migrations check) so we isolate the placeholder migration's behavior.
    """
    from database import Database
    Database._instance = None

    fixture_value = (
        'Custom default-flagged prompt with TAGLINE and PLATFORM-INSERTED ADS '
        'and brand tagline ads keywords so prior migrations skip it.'
    )
    db_path = tmp_path / "podcast.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(f"""
        CREATE TABLE settings (
            key TEXT PRIMARY KEY,
            value TEXT,
            is_default INTEGER DEFAULT 0,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE podcasts (id INTEGER PRIMARY KEY, slug TEXT);
        INSERT INTO podcasts(slug) VALUES ('seed-pod');
        INSERT INTO settings (key, value, is_default)
            VALUES ('system_prompt', '{fixture_value}', 1);
    """)
    conn.commit()
    conn.close()

    db = Database(data_dir=str(tmp_path))
    sp = db.get_setting('system_prompt')
    # The placeholder backfill must not touch default-flagged rows.
    assert '{sponsor_database}' not in sp
    # Value should be preserved verbatim by my migration (and by other
    # migrations that skipped this fixture).
    assert sp == fixture_value


def test_migration_skips_already_placeholdered_prompts(tmp_path):
    """Customized prompts that already include the placeholder stay verbatim."""
    from database import Database
    Database._instance = None

    db_path = tmp_path / "podcast.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE settings (
            key TEXT PRIMARY KEY,
            value TEXT,
            is_default INTEGER DEFAULT 0,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE podcasts (id INTEGER PRIMARY KEY, slug TEXT);
        INSERT INTO podcasts(slug) VALUES ('seed-pod');
        INSERT INTO settings (key, value, is_default)
            VALUES ('system_prompt', 'My prompt {sponsor_database} done', 0);
    """)
    conn.commit()
    conn.close()

    db = Database(data_dir=str(tmp_path))
    sp = db.get_setting('system_prompt')
    assert sp == 'My prompt {sponsor_database} done'
