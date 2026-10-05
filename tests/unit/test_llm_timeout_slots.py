"""Per-slot LLM timeout and retry settings (#806)."""
from unittest.mock import patch

from tests.app_bootstrap import bootstrap
bootstrap('llm_timeout_slots_test_')

import llm_client
from llm_client import get_llm_timeout, get_llm_max_retries


def _settings(values):
    return patch.object(llm_client, '_get_cached_setting', side_effect=lambda k: values.get(k))


def test_defaults_by_provider_type():
    with _settings({}):
        assert get_llm_timeout('anthropic', 'primary') == 120.0
        assert get_llm_timeout('ollama', 'secondary') == 600.0
        assert get_llm_max_retries('openrouter', 'primary') == 3
        assert get_llm_max_retries('openai-compatible', 'failover') == 2


def test_slot_setting_overrides_type_default():
    with _settings({'secondary_llm_timeout_seconds': '45', 'secondary_llm_max_retries': '0'}):
        assert get_llm_timeout('ollama', 'secondary') == 45.0
        assert get_llm_max_retries('ollama', 'secondary') == 0
        assert get_llm_timeout('ollama', 'primary') == 600.0


def test_garbage_setting_falls_back():
    with _settings({'llm_timeout_seconds': 'soon'}):
        assert get_llm_timeout('anthropic', 'primary') == 120.0


def test_no_args_matches_global_provider():
    with _settings({}), patch.object(llm_client, 'get_effective_provider', return_value='ollama'):
        assert get_llm_timeout() == 600.0
