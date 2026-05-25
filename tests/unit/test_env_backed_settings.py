"""Tests for the ENV_BACKED_SETTINGS registry + migration in
src/database/schema/__init__.py and src/config.py.

The migration's contract: on every boot, rows where is_default=1 re-sync
to the current env var value. Rows where is_default=0 are NEVER touched.
A one-shot corrective pass flips is_default for any divergent is_default=1
row WITHOUT changing the stored value, so no data is lost on any deployer.
"""
import os
import sqlite3
import shutil
import tempfile
from unittest.mock import patch

import pytest

from database import Database
import config


@pytest.fixture
def fresh_db_dir():
    d = tempfile.mkdtemp(prefix="minuspod_envbacked_test_")
    Database._instance = None
    yield d
    Database._instance = None
    shutil.rmtree(d, ignore_errors=True)


def _row(db_path, key):
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute(
            "SELECT value, is_default FROM settings WHERE key = ?", (key,)
        ).fetchone()


def _set_row(db_path, key, value, is_default):
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """INSERT INTO settings (key, value, is_default) VALUES (?, ?, ?)
               ON CONFLICT(key) DO UPDATE SET value = excluded.value,
                                              is_default = excluded.is_default""",
            (key, value, 1 if is_default else 0),
        )
        conn.commit()


def _delete_row(db_path, key):
    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM settings WHERE key = ?", (key,))
        conn.commit()


def _reload_db(d):
    Database._instance = None
    return Database(data_dir=d)


class TestResolveEnvBackedDefault:
    """Pure-function tests for config.resolve_env_backed_default."""

    def test_returns_env_value_when_set_and_valid(self):
        with patch.dict(os.environ, {'AUDIO_BITRATE': '192k'}, clear=False):
            assert config.resolve_env_backed_default('audio_bitrate') == '192k'

    def test_returns_fallback_when_env_unset(self):
        env = os.environ.copy()
        env.pop('AUDIO_BITRATE', None)
        with patch.dict(os.environ, env, clear=True):
            assert config.resolve_env_backed_default('audio_bitrate') == '128k'

    def test_returns_fallback_when_env_fails_validator(self):
        with patch.dict(os.environ, {'AUDIO_BITRATE': '999k'}, clear=False):
            assert config.resolve_env_backed_default('audio_bitrate') == '128k'

    def test_returns_none_for_unregistered_key(self):
        assert config.resolve_env_backed_default('totally_bogus_key') is None

    def test_parallel_windows_validator_rejects_out_of_range(self):
        with patch.dict(os.environ, {'AD_DETECTION_PARALLEL_WINDOWS': '0'}, clear=False):
            assert config.resolve_env_backed_default('ad_detection_parallel_windows') == '4'
        with patch.dict(os.environ, {'AD_DETECTION_PARALLEL_WINDOWS': '33'}, clear=False):
            assert config.resolve_env_backed_default('ad_detection_parallel_windows') == '4'
        with patch.dict(os.environ, {'AD_DETECTION_PARALLEL_WINDOWS': 'abc'}, clear=False):
            assert config.resolve_env_backed_default('ad_detection_parallel_windows') == '4'

    def test_parallel_windows_validator_accepts_in_range(self):
        with patch.dict(os.environ, {'AD_DETECTION_PARALLEL_WINDOWS': '8'}, clear=False):
            assert config.resolve_env_backed_default('ad_detection_parallel_windows') == '8'


class TestEnvBackedSettingsRegistry:
    """ENV_BACKED_SETTINGS minimum-key contract."""

    def test_registry_has_required_keys(self):
        keys = {db_key for db_key, _, _, _ in config.ENV_BACKED_SETTINGS}
        assert 'llm_provider' in keys
        assert 'audio_bitrate' in keys
        assert 'skip_flac_compression' in keys
        assert 'ad_detection_parallel_windows' in keys

    def test_each_entry_has_four_elements(self):
        for entry in config.ENV_BACKED_SETTINGS:
            assert len(entry) == 4
            db_key, env_var, fallback, validator = entry
            assert isinstance(db_key, str) and db_key
            assert isinstance(env_var, str) and env_var
            assert isinstance(fallback, str)
            assert validator is None or callable(validator)


class TestSchemaMigrationsTable:
    """schema_migrations table is created and marks the corrective pass."""

    def test_schema_migrations_table_exists_after_init(self, fresh_db_dir):
        _reload_db(fresh_db_dir)
        with sqlite3.connect(os.path.join(fresh_db_dir, "podcast.db")) as conn:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
            ).fetchone()
            assert row is not None

    def test_corrective_migration_marker_is_inserted(self, fresh_db_dir):
        _reload_db(fresh_db_dir)
        with sqlite3.connect(os.path.join(fresh_db_dir, "podcast.db")) as conn:
            row = conn.execute(
                "SELECT name FROM schema_migrations WHERE name = 'env_backed_settings_correct_flags'"
            ).fetchone()
            assert row is not None


class TestPerBootResync:
    """is_default=1 rows track env across restarts."""

    def test_is_default_1_row_updates_when_env_changes(self, fresh_db_dir):
        # Fresh DB seeds llm_provider from default env
        with patch.dict(os.environ, {'LLM_PROVIDER': 'anthropic'}, clear=False):
            _reload_db(fresh_db_dir)
        db_path = os.path.join(fresh_db_dir, "podcast.db")
        row = _row(db_path, 'llm_provider')
        assert row['value'] == 'anthropic'
        assert row['is_default'] == 1

        # User changes env, restart -- value tracks env
        with patch.dict(os.environ, {'LLM_PROVIDER': 'openai-compatible'}, clear=False):
            _reload_db(fresh_db_dir)
        row = _row(db_path, 'llm_provider')
        assert row['value'] == 'openai-compatible'
        assert row['is_default'] == 1

    def test_is_default_0_row_is_never_touched(self, fresh_db_dir):
        """The core data-preservation guarantee. A user customization
        survives any env change without modification."""
        with patch.dict(os.environ, {'LLM_PROVIDER': 'anthropic'}, clear=False):
            _reload_db(fresh_db_dir)
        db_path = os.path.join(fresh_db_dir, "podcast.db")

        # User customizes via UI: is_default=0
        _set_row(db_path, 'llm_provider', 'ollama', is_default=False)

        # Env changes underneath them
        with patch.dict(os.environ, {'LLM_PROVIDER': 'openai-compatible'}, clear=False):
            _reload_db(fresh_db_dir)

        row = _row(db_path, 'llm_provider')
        assert row['value'] == 'ollama', "Customized value must survive env change"
        assert row['is_default'] == 0


class TestCorrectivePass:
    """One-shot corrective: divergent is_default=1 rows are reclassified
    is_default=0 with value preserved."""

    def test_corrective_flips_flag_preserves_value(self, fresh_db_dir):
        # Simulate a pre-2.5.23 DB: is_default=1 but value diverged from env
        with patch.dict(os.environ, {'AUDIO_BITRATE': '128k'}, clear=False):
            _reload_db(fresh_db_dir)
        db_path = os.path.join(fresh_db_dir, "podcast.db")

        # Clear the corrective gate so a re-init will run it again with
        # a freshly-divergent row.
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "DELETE FROM schema_migrations WHERE name = 'env_backed_settings_correct_flags'"
            )
            conn.commit()

        # Plant a divergent is_default=1 row: value=192k but env says 128k
        _set_row(db_path, 'audio_bitrate', '192k', is_default=True)

        with patch.dict(os.environ, {'AUDIO_BITRATE': '128k'}, clear=False):
            _reload_db(fresh_db_dir)

        row = _row(db_path, 'audio_bitrate')
        assert row['value'] == '192k', "Corrective must preserve value, never overwrite"
        assert row['is_default'] == 0, "Corrective must flip is_default to 0"

    def test_corrective_runs_only_once(self, fresh_db_dir):
        """Idempotency: a second boot after the corrective ran will NOT
        re-touch a divergent is_default=1 row again."""
        with patch.dict(os.environ, {'AUDIO_BITRATE': '128k'}, clear=False):
            _reload_db(fresh_db_dir)
        db_path = os.path.join(fresh_db_dir, "podcast.db")

        # After first boot the gate is set. Plant a divergent is_default=1
        # row (which shouldn't naturally occur post-migration, but tests
        # the gate is honored).
        _set_row(db_path, 'audio_bitrate', '192k', is_default=True)

        with patch.dict(os.environ, {'AUDIO_BITRATE': '128k'}, clear=False):
            _reload_db(fresh_db_dir)

        # The gate is set, so corrective should NOT have flipped the flag.
        # Step 4 resync DOES run every boot, so the value will be updated
        # to env (this is the expected post-migration behavior).
        row = _row(db_path, 'audio_bitrate')
        assert row['is_default'] == 1, "Gate must prevent re-running corrective"
        assert row['value'] == '128k', "Resync updates is_default=1 to env value"


class TestSeedMissingKeys:
    """Newly-added registry keys are seeded on an existing DB without
    wiping anything else."""

    def test_missing_key_inserted_on_first_boot_with_new_key(self, fresh_db_dir):
        # First boot: registry has all four keys; one of them (parallel
        # windows) is deleted to simulate an upgrade where the key was
        # added in this release.
        with patch.dict(os.environ, {'AD_DETECTION_PARALLEL_WINDOWS': '4'}, clear=False):
            _reload_db(fresh_db_dir)
        db_path = os.path.join(fresh_db_dir, "podcast.db")
        _delete_row(db_path, 'ad_detection_parallel_windows')

        # Other keys must remain
        _set_row(db_path, 'llm_provider', 'ollama', is_default=False)

        # Re-init: missing key gets seeded from env, is_default=1
        with patch.dict(os.environ, {'AD_DETECTION_PARALLEL_WINDOWS': '8'}, clear=False):
            _reload_db(fresh_db_dir)

        row = _row(db_path, 'ad_detection_parallel_windows')
        assert row is not None, "New key must be seeded"
        assert row['value'] == '8'
        assert row['is_default'] == 1

        # Other user-customized key is untouched
        other = _row(db_path, 'llm_provider')
        assert other['value'] == 'ollama'
        assert other['is_default'] == 0
