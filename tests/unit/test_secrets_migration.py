"""Tests for plaintext -> encrypted secret migration."""
import tempfile
from pathlib import Path

import pytest

import secrets_crypto


@pytest.fixture
def crypto_env(monkeypatch, tmp_path):
    monkeypatch.setenv("MINUSPOD_MASTER_PASSPHRASE", "unit-test-passphrase")
    monkeypatch.setattr(
        secrets_crypto,
        "BACKUP_DIR",
        tmp_path / "backups",
    )
    secrets_crypto.reset_cache()
    yield
    secrets_crypto.reset_cache()


def test_migration_noop_on_fresh_db(temp_db, crypto_env):
    result = secrets_crypto.migrate_plaintext_secrets(temp_db)
    assert result == {"migrated": 0, "skipped": 0, "backup_path": None}


def test_migration_encrypts_plaintext(temp_db, crypto_env):
    temp_db.set_setting("whisper_api_key", "sk-test-plaintext-123")
    assert secrets_crypto.count_plaintext_secrets(temp_db) == 1

    result = secrets_crypto.migrate_plaintext_secrets(temp_db)
    assert result["migrated"] == 1
    assert result["skipped"] == 0
    assert result["backup_path"] and Path(result["backup_path"]).exists()

    stored = temp_db.get_setting("whisper_api_key")
    assert stored.startswith(secrets_crypto.ENVELOPE_PREFIX)
    assert temp_db.get_secret("whisper_api_key") == "sk-test-plaintext-123"


def test_migration_skips_already_encrypted(temp_db, crypto_env):
    temp_db.set_secret("openrouter_api_key", "sk-or-encrypted")
    assert secrets_crypto.count_plaintext_secrets(temp_db) == 0

    result = secrets_crypto.migrate_plaintext_secrets(temp_db)
    assert result["migrated"] == 0
    assert result["backup_path"] is None


def test_migration_idempotent(temp_db, crypto_env):
    temp_db.set_setting("anthropic_api_key", "sk-ant-legacy")
    first = secrets_crypto.migrate_plaintext_secrets(temp_db)
    assert first["migrated"] == 1

    second = secrets_crypto.migrate_plaintext_secrets(temp_db)
    assert second["migrated"] == 0
    assert second["backup_path"] is None


def test_migration_noop_without_passphrase(temp_db, monkeypatch):
    monkeypatch.delenv("MINUSPOD_MASTER_PASSPHRASE", raising=False)
    secrets_crypto.reset_cache()
    temp_db.set_setting("openai_api_key", "sk-plaintext")

    result = secrets_crypto.migrate_plaintext_secrets(temp_db)
    assert result == {"migrated": 0, "skipped": 0, "backup_path": None}
    assert temp_db.get_setting("openai_api_key") == "sk-plaintext"


def test_count_skips_non_secret_keys(temp_db, crypto_env):
    temp_db.set_setting("some_arbitrary_setting", "not-a-secret")
    temp_db.set_setting("whisper_api_key", "")
    assert secrets_crypto.count_plaintext_secrets(temp_db) == 0


def test_backup_failure_aborts_migration(temp_db, crypto_env, monkeypatch):
    temp_db.set_setting("whisper_api_key", "sk-plaintext-should-survive")
    monkeypatch.setattr(
        secrets_crypto, "BACKUP_DIR", Path("/nonexistent/read-only/path")
    )

    result = secrets_crypto.migrate_plaintext_secrets(temp_db)
    assert result["migrated"] == 0
    assert result["backup_path"] is None
    assert temp_db.get_setting("whisper_api_key") == "sk-plaintext-should-survive"


def test_set_secret_does_not_double_encrypt_ciphertext(temp_db, crypto_env):
    """A UI round-trip that replays an already-encrypted blob must not be
    wrapped again, otherwise decrypt returns another ciphertext string that
    downstream clients would send as an API key."""
    temp_db.set_secret("openai_api_key", "sk-real-plaintext")
    envelope = temp_db.get_setting("openai_api_key")
    assert envelope.startswith(secrets_crypto.ENVELOPE_PREFIX)

    temp_db.set_secret("openai_api_key", envelope)
    assert temp_db.get_setting("openai_api_key") == envelope
    assert temp_db.get_secret("openai_api_key") == "sk-real-plaintext"
