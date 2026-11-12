import re
import sqlite3

from database.settings import SETTINGS_REGISTRY
from scripts.demo.export_allowlist import (
    DEMO_SETTINGS_ALLOW,
    DEMO_SETTINGS_EXCLUDE,
    EPISODE_REF,
    EXPORT_TABLES,
    DROPPED_TABLE_PREFIXES,
    PODCAST_REF,
    SETTINGS_FORCE,
    SETTINGS_FORCE_UNREGISTERED,
    create_full_schema,
)
from secrets_crypto import SECRET_SETTING_KEYS

DENY = re.compile(r'(_api_key|_secret|password|_url|webhook|email_|_dest|salt|flask_|auth_|youtube_proxy|ytdlp_extra_args)')

EMPTIED = {
    'processing_runs', 'upload_reservations', 'auto_process_queue',
    'provider_spend_reservations', 'cue_candidate_scans', 'cue_threshold_scans',
    'cue_cross_episode_scans', 'cue_window_optimize_scans', 'auth_failures',
    'search_index_changes', 'search_index', 'text_patterns',
    '_orphaned_audio_fingerprints', 'episode_feed_details',
    'audio_fingerprint_matches', 'deferred_upgrade_steps',
}

COLUMN_DROPS = {
    'podcasts': {
        'download_user_agent_override', 'feed_user_agent_override',
        'detection_notes', 'chapter_notes', 'last_refresh_error',
    },
    'episodes': {'error_message', 'sb_last_error', 'quality_notes', 'chapters_regen_error'},
    'processing_history': {'error_message', 'log_file'},
    'audio_cue_templates': {'pcm_blob'},
    'llm_call_usage': {'rate_snapshot'},
    'systemone_call_diagnostics': {'diagnostics_json'},
    'pattern_cleanup_runs': {'error'},
    'audio_fingerprints': {
        'source_podcast_slug', 'source_episode_id', 'source_start', 'source_end',
    },
}

PROMPT_OVERRIDES = {
    'system_prompt_override', 'verification_prompt_override',
    'review_prompt_override', 'resurrect_prompt_override',
    'chapter_prompt_override',
}


def _full_conn(tmp_path):
    return sqlite3.connect(create_full_schema(tmp_path / 'full'))


def _schema_columns(table, tmp_path):
    conn = _full_conn(tmp_path)
    return [r[1] for r in conn.execute(f'PRAGMA table_info({table})')]


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


def test_prompt_overrides_and_timezone_are_not_allowed():
    assert not ((PROMPT_OVERRIDES | {'notification_timezone'}) & DEMO_SETTINGS_ALLOW)


def test_unregistered_forced_keys_are_safe():
    for key in SETTINGS_FORCE_UNREGISTERED:
        assert key not in SETTINGS_REGISTRY, key
        assert key not in DEMO_SETTINGS_ALLOW, key
        assert key not in SECRET_SETTING_KEYS, key
        if key != 'email_enabled':  # forced on purpose; matches the email_ deny prefix
            assert not DENY.search(key), key
    assert not (set(SETTINGS_FORCE) & set(SETTINGS_FORCE_UNREGISTERED))


def test_every_schema_table_is_classified(tmp_path):
    conn = _full_conn(tmp_path)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")}
    unclassified = {t for t in tables if t not in EXPORT_TABLES and not t.startswith(DROPPED_TABLE_PREFIXES)}
    assert unclassified == set()


def test_emptied_tables_are_exactly_those_with_no_columns():
    assert {t for t, cols in EXPORT_TABLES.items() if cols == []} == EMPTIED


def test_kept_columns_exist(tmp_path):
    conn = _full_conn(tmp_path)
    for table, cols in EXPORT_TABLES.items():
        have = {r[1] for r in conn.execute(f'PRAGMA table_info({table})')}
        assert set(cols) <= have, table


def test_dropped_columns_are_absent_and_others_kept(tmp_path):
    for table, dropped in COLUMN_DROPS.items():
        all_cols = _schema_columns(table, tmp_path)
        assert dropped <= set(all_cols), table
        assert not (dropped & set(EXPORT_TABLES[table])), table
        assert set(EXPORT_TABLES[table]) == set(all_cols) - dropped, table
    assert 'mfcc_blob' in EXPORT_TABLES['audio_cue_templates']


def test_subset_tables_keep_exact_columns():
    assert set(EXPORT_TABLES['feed_subscriber_keys']) == {
        'id', 'podcast_id', 'created_at', 'last_used_at', 'revoked_at',
        'secret_hash', 'label',
    }
    assert set(EXPORT_TABLES['ip_geo_cache']) == {
        'ip', 'country', 'looked_up_at', 'status',
    }


def test_podcast_ref_covers_tables_with_podcast_columns(tmp_path):
    conn = _full_conn(tmp_path)
    for table in EXPORT_TABLES:
        if table.startswith(DROPPED_TABLE_PREFIXES):
            continue
        cols = {r[1] for r in conn.execute(f'PRAGMA table_info({table})')}
        if cols & {'podcast_id', 'podcast_slug'}:
            assert table in PODCAST_REF, table


def test_migrated_columns_are_kept():
    assert {'published_at', 'reprocess_mode', 'detection_degraded'} <= set(EXPORT_TABLES['episodes'])


def test_ref_key_kinds_match_column_types(tmp_path):
    conn = _full_conn(tmp_path)
    want = {'id': 'INTEGER', 'slug': 'TEXT'}
    refs = [(t, c, k) for t, (c, k) in PODCAST_REF.items()]
    refs += [(t, c, k) for t, (c, _e, k) in EPISODE_REF.items() if c]
    for table, col, kind in refs:
        decl = {r[1]: r[2].upper() for r in conn.execute(f'PRAGMA table_info({table})')}[col]
        assert decl == want[kind], (table, col, kind, decl)
