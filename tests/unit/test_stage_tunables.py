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

    def test_caller_supplied_dict_shape(self, monkeypatch):
        # api.settings GET handler passes raw get_all_settings() entries:
        # {'value': str, 'is_default': bool}.
        _clear_envs(monkeypatch, "DETECTION_MAX_TOKENS", "AD_DETECTION_MAX_TOKENS")
        settings = {"detection_max_tokens": {"value": "8192", "is_default": False}}
        assert config.get_stage_tunable("detection_max_tokens", settings=settings) == 8192

    def test_caller_supplied_setting_entry_shape(self, monkeypatch):
        # api.settings._settings_view wraps the raw dict into a SettingEntry
        # dataclass. The resolver must read .value off it; before this
        # regression test, the resolver only handled the dict shape and
        # silently fell back to the default for the SettingEntry path.
        from dataclasses import dataclass

        @dataclass(frozen=True)
        class FakeEntry:
            value: object
            is_default: bool

        _clear_envs(monkeypatch, "DETECTION_MAX_TOKENS", "AD_DETECTION_MAX_TOKENS")
        settings = {"detection_max_tokens": FakeEntry(value="8192", is_default=False)}
        assert config.get_stage_tunable("detection_max_tokens", settings=settings) == 8192


class TestStageTunableReset:
    """reset_setting must clear stage-tunable rows (not silently no-op).

    Before the fix, reset_setting's hardcoded defaults dict omitted every stage
    tunable, so reset_setting('reviewer_max_tokens') fell through to return False
    and left the user's value in place -- the "Reset All" no-op behind issue #351.
    """

    def test_reset_clears_numeric_tunable(self, temp_db):
        temp_db.set_setting('reviewer_max_tokens', '16384', is_default=False)
        assert temp_db.reset_setting('reviewer_max_tokens') is True
        row = temp_db.get_all_settings()['reviewer_max_tokens']
        assert row['value'] == ''
        assert row['is_default'] is True

    def test_reset_clears_non_numeric_tunable_without_none_string(self, temp_db):
        # The default for reasoning level is None; clearing must write "" not "None".
        temp_db.set_setting('reviewer_reasoning_level', 'high', is_default=False)
        assert temp_db.reset_setting('reviewer_reasoning_level') is True
        assert temp_db.get_setting('reviewer_reasoning_level') == ''

    def test_reset_restores_default_resolution(self, temp_db, monkeypatch):
        _clear_envs(monkeypatch, 'REVIEWER_MAX_TOKENS', 'REVIEW_MAX_TOKENS')
        temp_db.set_setting('reviewer_max_tokens', '16384', is_default=False)
        temp_db.reset_setting('reviewer_max_tokens')
        settings = temp_db.get_all_settings()
        assert config.get_stage_tunable('reviewer_max_tokens', settings=settings) == 4096

    def test_reset_loop_clears_all_stage_tunables(self, temp_db):
        # Mirrors the loop in api.settings.reset_ad_detection_settings.
        from config import STAGE_TUNABLE_PAYLOAD_KEYS
        temp_db.set_setting('reviewer_max_tokens', '16384', is_default=False)
        temp_db.set_setting('window_size_seconds', '900', is_default=False)
        temp_db.set_setting('detection_temperature', '0.7', is_default=False)
        for _payload_key, db_key, _kind in STAGE_TUNABLE_PAYLOAD_KEYS:
            assert temp_db.reset_setting(db_key) is True
        alls = temp_db.get_all_settings()
        for key in ('reviewer_max_tokens', 'window_size_seconds', 'detection_temperature'):
            assert alls[key]['value'] == ''
            assert alls[key]['is_default'] is True

    def test_reset_setting_returns_false_for_unknown_key(self, temp_db):
        assert temp_db.reset_setting('not_a_real_setting') is False


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
