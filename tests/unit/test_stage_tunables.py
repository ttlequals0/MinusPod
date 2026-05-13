"""Tests for per-stage tunable config: env > DB > default + range validation."""
import os
from unittest.mock import patch

import pytest

import config


@pytest.fixture(autouse=True)
def _flush_provider_cache():
    # get_stage_tunable now routes DB lookups through llm_client's 5s TTL
    # cache. Tests patch database.Database fresh each time, so the cache must
    # be flushed to avoid bleed-through between test cases.
    try:
        from llm_client import _clear_provider_cache
        _clear_provider_cache()
    except Exception:
        pass
    yield
    try:
        from llm_client import _clear_provider_cache
        _clear_provider_cache()
    except Exception:
        pass


def _clear_envs(monkeypatch, *keys):
    for key in keys:
        monkeypatch.delenv(key, raising=False)


class TestDefaults:
    def test_detection_defaults(self, monkeypatch):
        _clear_envs(monkeypatch, "DETECTION_TEMPERATURE", "DETECTION_MAX_TOKENS",
                    "AD_DETECTION_MAX_TOKENS")
        with patch("database.Database", side_effect=Exception("no db in test")):
            assert config.get_stage_tunable("detection_temperature") == 0.0
            assert config.get_stage_tunable("detection_max_tokens") == 4096

    def test_chapter_defaults(self, monkeypatch):
        _clear_envs(monkeypatch, "CHAPTER_BOUNDARY_TEMPERATURE",
                    "CHAPTER_BOUNDARY_MAX_TOKENS", "CHAPTER_TITLE_TEMPERATURE",
                    "CHAPTER_TITLE_MAX_TOKENS")
        with patch("database.Database", side_effect=Exception("no db in test")):
            assert config.get_stage_tunable("chapter_boundary_temperature") == 0.1
            assert config.get_stage_tunable("chapter_boundary_max_tokens") == 300
            assert config.get_stage_tunable("chapter_title_temperature") == 0.3
            assert config.get_stage_tunable("chapter_title_max_tokens") == 500

    def test_reasoning_defaults_are_none(self, monkeypatch):
        _clear_envs(monkeypatch, "DETECTION_REASONING_BUDGET",
                    "DETECTION_REASONING_LEVEL")
        with patch("database.Database", side_effect=Exception("no db in test")):
            assert config.get_stage_tunable("detection_reasoning_budget") is None
            assert config.get_stage_tunable("detection_reasoning_level") is None

    def test_ollama_num_ctx_default_is_none(self, monkeypatch):
        _clear_envs(monkeypatch, "OLLAMA_NUM_CTX")
        with patch("database.Database", side_effect=Exception("no db in test")):
            assert config.get_stage_tunable("ollama_num_ctx") is None

    def test_unknown_key_raises(self):
        with pytest.raises(KeyError):
            config.get_stage_tunable("definitely_not_a_real_key")


class TestEnvOverride:
    def test_env_var_wins(self, monkeypatch):
        monkeypatch.setenv("DETECTION_MAX_TOKENS", "2048")
        with patch("database.Database", side_effect=Exception("db not consulted")):
            assert config.get_stage_tunable("detection_max_tokens") == 2048

    def test_legacy_alias_still_works(self, monkeypatch):
        monkeypatch.delenv("DETECTION_MAX_TOKENS", raising=False)
        monkeypatch.setenv("AD_DETECTION_MAX_TOKENS", "8192")
        with patch("database.Database", side_effect=Exception("db not consulted")):
            assert config.get_stage_tunable("detection_max_tokens") == 8192

    def test_env_var_out_of_range_falls_back_to_default(self, monkeypatch, caplog):
        monkeypatch.setenv("DETECTION_MAX_TOKENS", "999999")
        with patch("database.Database", side_effect=Exception("no db")):
            assert config.get_stage_tunable("detection_max_tokens") == 4096
        # WARNING should be logged.
        assert any("out of range" in r.message for r in caplog.records)

    def test_env_var_non_numeric_falls_back_to_default(self, monkeypatch, caplog):
        monkeypatch.setenv("DETECTION_TEMPERATURE", "kinda_hot")
        with patch("database.Database", side_effect=Exception("no db")):
            assert config.get_stage_tunable("detection_temperature") == 0.0

    def test_reasoning_level_enum_validation(self, monkeypatch, caplog):
        monkeypatch.setenv("DETECTION_REASONING_LEVEL", "ultra")
        with patch("database.Database", side_effect=Exception("no db")):
            assert config.get_stage_tunable("detection_reasoning_level") is None

    def test_reasoning_level_valid(self, monkeypatch):
        monkeypatch.setenv("DETECTION_REASONING_LEVEL", "Medium")
        with patch("database.Database", side_effect=Exception("no db")):
            assert config.get_stage_tunable("detection_reasoning_level") == "medium"

    def test_empty_env_falls_through(self, monkeypatch):
        monkeypatch.setenv("DETECTION_MAX_TOKENS", "")
        with patch("database.Database", side_effect=Exception("no db")):
            assert config.get_stage_tunable("detection_max_tokens") == 4096


class TestDbFallback:
    def test_db_value_used_when_env_unset(self, monkeypatch):
        _clear_envs(monkeypatch, "DETECTION_MAX_TOKENS", "AD_DETECTION_MAX_TOKENS")

        class FakeDB:
            def get_setting(self, key):
                return "16384" if key == "detection_max_tokens" else None

        with patch("database.Database", return_value=FakeDB()):
            assert config.get_stage_tunable("detection_max_tokens") == 16384

    def test_db_out_of_range_returns_default(self, monkeypatch, caplog):
        _clear_envs(monkeypatch, "DETECTION_MAX_TOKENS", "AD_DETECTION_MAX_TOKENS")

        class FakeDB:
            def get_setting(self, key):
                return "999999"

        with patch("database.Database", return_value=FakeDB()):
            assert config.get_stage_tunable("detection_max_tokens") == 4096

    def test_db_failure_returns_default(self, monkeypatch):
        _clear_envs(monkeypatch, "DETECTION_MAX_TOKENS", "AD_DETECTION_MAX_TOKENS")
        with patch("database.Database", side_effect=Exception("disk full")):
            assert config.get_stage_tunable("detection_max_tokens") == 4096


class TestEnvOverrideDetection:
    def test_no_override_when_env_unset(self, monkeypatch):
        _clear_envs(monkeypatch, "DETECTION_MAX_TOKENS", "AD_DETECTION_MAX_TOKENS")
        assert config.stage_tunable_env_override("detection_max_tokens") is None

    def test_returns_canonical_env_name(self, monkeypatch):
        monkeypatch.setenv("DETECTION_MAX_TOKENS", "2048")
        assert config.stage_tunable_env_override("detection_max_tokens") == "DETECTION_MAX_TOKENS"

    def test_legacy_alias_detected(self, monkeypatch):
        monkeypatch.delenv("DETECTION_MAX_TOKENS", raising=False)
        monkeypatch.setenv("AD_DETECTION_MAX_TOKENS", "8192")
        assert config.stage_tunable_env_override("detection_max_tokens") == "AD_DETECTION_MAX_TOKENS"

    def test_unknown_key_returns_none(self):
        assert config.stage_tunable_env_override("not_a_key") is None
