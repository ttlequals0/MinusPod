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
    'pricing_revision', 'state', 'credential_slot',
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


def test_credential_slot_added_to_legacy_row_without_data_loss(tmp_path):
    """A pre-credential_slot ledger row survives the additive migration with
    the new column NULL (read as 'primary')."""
    Database._instance = None
    db = Database(data_dir=str(tmp_path))
    try:
        conn = db.get_connection()
        conn.execute("DROP TABLE IF EXISTS llm_call_usage")
        conn.execute(
            """CREATE TABLE llm_call_usage (
                   attempt_id TEXT PRIMARY KEY, provider_key TEXT NOT NULL,
                   configured_model TEXT NOT NULL, phase_key TEXT NOT NULL,
                   created_at TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'in_flight')""")
        conn.execute(
            "INSERT INTO llm_call_usage (attempt_id, provider_key, "
            "configured_model, phase_key, created_at) VALUES "
            "('a1', 'anthropic', 'm', 'detect', '2026-01-01T00:00:00Z')")
        conn.commit()

        cols = db._get_table_columns(conn, 'llm_call_usage')
        assert db._add_column_if_missing(
            conn, 'llm_call_usage', 'credential_slot', 'TEXT', cols)

        row = conn.execute(
            "SELECT provider_key, credential_slot FROM llm_call_usage "
            "WHERE attempt_id = 'a1'").fetchone()
        assert row['provider_key'] == 'anthropic'
        assert row['credential_slot'] is None
        assert db.count_recent_llm_attempts(
            'anthropic', 'primary', '2026-01-01T00:00:00Z') == 1
    finally:
        Database._instance = None
