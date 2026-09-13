"""Tests for the additive llm_call_usage ledger table migration."""
from tests.app_bootstrap import bootstrap

bootstrap('llm_call_usage_migration_test_')

from database import Database

EXPECTED_COLUMNS = {
    'attempt_id', 'run_id', 'podcast_id', 'episode_id', 'created_at',
    'finalized_at', 'phase_key', 'invoking_pass', 'window_label',
    'provider_key', 'configured_model', 'returned_model', 'input_tokens',
    'output_tokens', 'cache_read_tokens', 'cache_write_tokens',
    'reasoning_tokens', 'cost_usd', 'cost_source', 'rate_snapshot',
    'pricing_revision', 'state',
}


def test_llm_call_usage_created_on_fresh_db(tmp_path):
    Database._instance = None
    db = Database(data_dir=str(tmp_path))
    try:
        cols = {r['name'] for r in
                db.get_connection().execute("PRAGMA table_info(llm_call_usage)")}
        assert EXPECTED_COLUMNS <= cols
    finally:
        Database._instance = None


def test_llm_call_usage_created_on_existing_db(tmp_path):
    Database._instance = None
    db = Database(data_dir=str(tmp_path))
    try:
        conn = db.get_connection()
        conn.execute("DROP TABLE IF EXISTS llm_call_usage")
        conn.commit()
        assert not db._table_exists(conn, 'llm_call_usage')

        db._create_new_tables_only(conn)

        assert db._table_exists(conn, 'llm_call_usage')
    finally:
        Database._instance = None


def test_migration_is_idempotent(tmp_path):
    Database._instance = None
    db = Database(data_dir=str(tmp_path))
    try:
        conn = db.get_connection()
        db._create_new_tables_only(conn)
        db._create_new_tables_only(conn)  # second run must not raise
        assert db._table_exists(conn, 'llm_call_usage')
    finally:
        Database._instance = None
