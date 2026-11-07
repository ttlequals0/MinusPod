import re
from database.settings import SETTINGS_REGISTRY
from database.schema.tables import TABLE_DDL
from secrets_crypto import SECRET_SETTING_KEYS
from scripts.demo.export_allowlist import (
    DEMO_SETTINGS_ALLOW, DEMO_SETTINGS_EXCLUDE, EXPORT_TABLES, SETTINGS_FORCE, PODCAST_REF,
)

DENY = re.compile(r'(_api_key|_secret|password|_url|webhook|email_|_dest|salt|flask_|auth_|youtube_proxy|ytdlp_extra_args)')

def test_no_secret_setting_is_allowed():
    assert not (set(SECRET_SETTING_KEYS) & DEMO_SETTINGS_ALLOW)

def test_no_deny_pattern_in_allowed_settings():
    assert [k for k in DEMO_SETTINGS_ALLOW if DENY.search(k)] == []

def test_allowed_and_forced_keys_exist_in_registry():
    assert DEMO_SETTINGS_ALLOW <= set(SETTINGS_REGISTRY)
    assert set(SETTINGS_FORCE) <= set(SETTINGS_REGISTRY)

def test_every_registry_setting_is_classified():
    unclassified = {
        k for k in SETTINGS_REGISTRY
        if k not in DEMO_SETTINGS_ALLOW and k not in DEMO_SETTINGS_EXCLUDE and not DENY.search(k)
    }
    assert unclassified == set()
    assert not (DEMO_SETTINGS_ALLOW & DEMO_SETTINGS_EXCLUDE)

def test_every_schema_table_is_classified():
    assert set(TABLE_DDL) <= set(EXPORT_TABLES)

def test_kept_columns_exist(tmp_path):
    import sqlite3
    from database.schema.tables import SCHEMA_SQL
    conn = sqlite3.connect(tmp_path / 'x.db')
    conn.executescript(SCHEMA_SQL)
    for table, cols in EXPORT_TABLES.items():
        have = {r[1] for r in conn.execute(f'PRAGMA table_info({table})')}
        assert set(cols) <= have, table

def test_podcast_ref_covers_tables_with_podcast_columns(tmp_path):
    import sqlite3
    from database.schema.tables import SCHEMA_SQL
    conn = sqlite3.connect(tmp_path / 'x.db')
    conn.executescript(SCHEMA_SQL)
    for table in EXPORT_TABLES:
        cols = {r[1] for r in conn.execute(f'PRAGMA table_info({table})')}
        if cols & {'podcast_id', 'podcast_slug'}:
            assert table in PODCAST_REF, table
