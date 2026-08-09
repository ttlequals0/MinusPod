"""Tests for the 2.86.3 upgrade migrations in
src/database/schema/__init__.py:

1. `clear_seeded_model_defaults`: deletes is_default=1 rows for the three
   model settings so a stale system-chosen model id does not survive into
   the require-explicit-model contract.
2. LLM_PROVIDER adoption: the pre-existing ENV_BACKED_SETTINGS per-boot
   resync now validates the env value (config._validate_llm_provider)
   before adopting it into the is_default=1 `llm_provider` row, so a typo
   falls back safely with a warning instead of being written verbatim.
"""
import logging

import pytest

from tests.app_bootstrap import bootstrap

bootstrap('model_settings_migration_test_')

from database import Database

MODEL_KEYS = ('claude_model', 'verification_model', 'chapters_model')


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv('OPENAI_MODEL', 'test-model')
    Database._instance = None
    instance = Database(data_dir=str(tmp_path))
    yield instance
    Database._instance = None


def _row(db, key):
    conn = db.get_connection()
    return conn.execute(
        "SELECT value, is_default FROM settings WHERE key = ?", (key,)
    ).fetchone()


def _set_row(db, key, value, is_default):
    conn = db.get_connection()
    conn.execute(
        """INSERT INTO settings (key, value, is_default) VALUES (?, ?, ?)
           ON CONFLICT(key) DO UPDATE SET value = excluded.value,
                                          is_default = excluded.is_default""",
        (key, value, 1 if is_default else 0),
    )
    conn.commit()


def _clear_gate(db, name):
    conn = db.get_connection()
    conn.execute("DELETE FROM schema_migrations WHERE name = ?", (name,))
    conn.commit()


class TestClearSeededModelDefaults:
    def test_seeded_row_is_deleted(self, db):
        _set_row(db, 'claude_model', 'claude-sonnet-4-5-20250929', is_default=True)
        _clear_gate(db, 'clear_seeded_model_defaults')

        db._run_schema_migrations()

        assert _row(db, 'claude_model') is None
        assert db.get_setting('claude_model') is None

    def test_operator_set_row_survives(self, db):
        _set_row(db, 'claude_model', 'claude-opus-4-6', is_default=False)
        _clear_gate(db, 'clear_seeded_model_defaults')

        db._run_schema_migrations()

        row = _row(db, 'claude_model')
        assert row is not None
        assert row['value'] == 'claude-opus-4-6'
        assert row['is_default'] == 0

    def test_all_three_keys_cleared_when_seeded(self, db):
        for key in MODEL_KEYS:
            _set_row(db, key, 'stale-model-id', is_default=True)
        _clear_gate(db, 'clear_seeded_model_defaults')

        db._run_schema_migrations()

        for key in MODEL_KEYS:
            assert _row(db, key) is None

    def test_fresh_db_with_no_seeded_rows_is_unaffected(self, tmp_path, monkeypatch):
        monkeypatch.delenv('OPENAI_MODEL', raising=False)
        Database._instance = None
        fresh_db = Database(data_dir=str(tmp_path))
        try:
            for key in MODEL_KEYS:
                assert _row(fresh_db, key) is None
            gate = fresh_db.get_connection().execute(
                "SELECT 1 FROM schema_migrations WHERE name = 'clear_seeded_model_defaults'"
            ).fetchone()
            assert gate is not None
        finally:
            Database._instance = None

    def test_idempotent_second_run_does_not_clear_a_row_set_in_between(self, db):
        _set_row(db, 'claude_model', 'stale-model-id', is_default=True)
        _clear_gate(db, 'clear_seeded_model_defaults')
        db._run_schema_migrations()
        assert _row(db, 'claude_model') is None

        # Operator configures a model after the migration already ran.
        _set_row(db, 'claude_model', 'claude-opus-4-6', is_default=False)
        db._run_schema_migrations()

        row = _row(db, 'claude_model')
        assert row['value'] == 'claude-opus-4-6'
        assert row['is_default'] == 0


class TestProviderAdoption:
    def test_env_set_and_differs_and_is_default_adopts(self, db, monkeypatch):
        _set_row(db, 'llm_provider', 'anthropic', is_default=True)
        monkeypatch.setenv('LLM_PROVIDER', 'openai-compatible')

        db._run_schema_migrations()

        row = _row(db, 'llm_provider')
        assert row['value'] == 'openai-compatible'
        assert row['is_default'] == 1

    def test_operator_set_row_never_overridden(self, db, monkeypatch):
        _set_row(db, 'llm_provider', 'ollama', is_default=False)
        monkeypatch.setenv('LLM_PROVIDER', 'openai-compatible')

        db._run_schema_migrations()

        row = _row(db, 'llm_provider')
        assert row['value'] == 'ollama'
        assert row['is_default'] == 0

    def test_env_unset_leaves_row_untouched(self, db, monkeypatch):
        _set_row(db, 'llm_provider', 'anthropic', is_default=True)
        monkeypatch.delenv('LLM_PROVIDER', raising=False)

        db._run_schema_migrations()

        row = _row(db, 'llm_provider')
        assert row['value'] == 'anthropic'
        assert row['is_default'] == 1

    def test_unknown_env_value_leaves_row_untouched_and_warns(self, db, monkeypatch, caplog):
        _set_row(db, 'llm_provider', 'anthropic', is_default=True)
        monkeypatch.setenv('LLM_PROVIDER', 'not-a-real-provider')

        with caplog.at_level(logging.WARNING):
            db._run_schema_migrations()

        row = _row(db, 'llm_provider')
        assert row['value'] == 'anthropic', "invalid env value must never be adopted verbatim"
        assert row['is_default'] == 1
        assert any('not-a-real-provider' in r.message for r in caplog.records)

    def test_idempotent_second_run_does_not_clobber_a_choice_made_in_between(self, db, monkeypatch):
        monkeypatch.setenv('LLM_PROVIDER', 'openai-compatible')
        db._run_schema_migrations()
        assert _row(db, 'llm_provider')['value'] == 'openai-compatible'

        # Operator picks a provider via the UI between boots.
        _set_row(db, 'llm_provider', 'ollama', is_default=False)
        db._run_schema_migrations()

        row = _row(db, 'llm_provider')
        assert row['value'] == 'ollama'
        assert row['is_default'] == 0
