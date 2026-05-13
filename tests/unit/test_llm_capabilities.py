"""Tests for llm_capabilities: per-pass fallback flag, defaults, provider translation."""
import pytest

import llm_capabilities
from llm_capabilities import (
    PASS_AD_DETECTION_1,
    PASS_AD_DETECTION_2,
    PASS_CHAPTER_GENERATION,
    PASS_REVIEWER_1,
    PASS_REVIEWER_2,
    clear_fallback,
    get_pass_defaults,
    is_fallback_eligible_error,
    is_fallback_set,
    set_fallback,
    translate_reasoning_effort,
)


@pytest.fixture(autouse=True)
def _reset_fallback_state():
    with llm_capabilities._fallback_lock:
        llm_capabilities._fallback_state.clear()


class TestFallbackFlag:
    def test_default_is_unset(self):
        assert is_fallback_set("ep1", PASS_AD_DETECTION_1) is False

    def test_set_and_check(self):
        set_fallback("ep1", PASS_AD_DETECTION_1)
        assert is_fallback_set("ep1", PASS_AD_DETECTION_1) is True

    def test_clear_clears_only_named_pass(self):
        set_fallback("ep1", PASS_AD_DETECTION_1)
        set_fallback("ep1", PASS_REVIEWER_1)
        clear_fallback("ep1", PASS_AD_DETECTION_1)
        assert is_fallback_set("ep1", PASS_AD_DETECTION_1) is False
        assert is_fallback_set("ep1", PASS_REVIEWER_1) is True

    def test_episode_scoping(self):
        set_fallback("ep1", PASS_AD_DETECTION_1)
        assert is_fallback_set("ep1", PASS_AD_DETECTION_1) is True
        assert is_fallback_set("ep2", PASS_AD_DETECTION_1) is False

    def test_clear_unset_is_noop(self):
        clear_fallback("ep1", PASS_AD_DETECTION_1)
        assert is_fallback_set("ep1", PASS_AD_DETECTION_1) is False

    def test_episode_id_coerced_to_str(self):
        set_fallback(42, PASS_AD_DETECTION_1)
        assert is_fallback_set(42, PASS_AD_DETECTION_1) is True
        assert is_fallback_set("42", PASS_AD_DETECTION_1) is True


class TestPassDefaults:
    def test_detection_pass1(self):
        d = get_pass_defaults(PASS_AD_DETECTION_1)
        assert d.temperature == 0.0
        assert d.max_tokens == 4096

    def test_detection_pass2_matches_pass1(self):
        # Verification reused AD_DETECTION_MAX_TOKENS in the old code; preserve.
        d = get_pass_defaults(PASS_AD_DETECTION_2)
        assert d.max_tokens == 4096

    def test_reviewer_passes_share_defaults(self):
        assert get_pass_defaults(PASS_REVIEWER_1) == get_pass_defaults(PASS_REVIEWER_2)
        assert get_pass_defaults(PASS_REVIEWER_1).max_tokens == 4096

    def test_chapters(self):
        d = get_pass_defaults(PASS_CHAPTER_GENERATION)
        assert d.temperature == 0.1
        assert d.max_tokens == 300

    def test_unknown_pass_raises(self):
        with pytest.raises(ValueError):
            get_pass_defaults("not_a_real_pass")


class TestTranslateReasoningEffort:
    def test_none_returns_empty(self):
        assert translate_reasoning_effort("anthropic", None) == {}
        assert translate_reasoning_effort("openrouter", None) == {}
        assert translate_reasoning_effort("ollama", None) == {}

    def test_anthropic_int_emits_thinking_block(self):
        assert translate_reasoning_effort("anthropic", 8192) == {
            "thinking": {"type": "enabled", "budget_tokens": 8192}
        }

    def test_anthropic_string_silently_drops(self):
        assert translate_reasoning_effort("anthropic", "high") == {}

    def test_openai_compatible_string(self):
        assert translate_reasoning_effort("openai-compatible", "low") == {
            "reasoning_effort": "low"
        }

    def test_openrouter_string(self):
        assert translate_reasoning_effort("openrouter", "medium") == {
            "extra_body": {"reasoning": {"effort": "medium"}}
        }

    def test_ollama_none_string_disables_thinking(self):
        assert translate_reasoning_effort("ollama", "none") == {
            "extra_body": {"options": {"think": False}}
        }

    @pytest.mark.parametrize("level", ["low", "medium", "high"])
    def test_ollama_levels_enable_thinking(self, level):
        assert translate_reasoning_effort("ollama", level) == {
            "extra_body": {"options": {"think": True}}
        }

    def test_unknown_level_drops(self):
        assert translate_reasoning_effort("openrouter", "extreme") == {}

    def test_case_insensitive(self):
        assert translate_reasoning_effort("ANTHROPIC", 1024) == {
            "thinking": {"type": "enabled", "budget_tokens": 1024}
        }
        assert translate_reasoning_effort("Openrouter", "HIGH") == {
            "extra_body": {"reasoning": {"effort": "high"}}
        }


class _StatusError(Exception):
    def __init__(self, status_code):
        self.status_code = status_code


class _ResponseError(Exception):
    """Some SDKs put status_code on .response, not on the exception."""
    def __init__(self, status_code):
        self.response = type("R", (), {"status_code": status_code})()


class TestErrorClassifier:
    @pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
    def test_4xx_non_rate_limit_is_eligible(self, status):
        assert is_fallback_eligible_error(_StatusError(status)) is True

    def test_429_is_not_eligible(self):
        assert is_fallback_eligible_error(_StatusError(429)) is False

    @pytest.mark.parametrize("status", [500, 502, 503, 504, 529])
    def test_5xx_is_not_eligible(self, status):
        assert is_fallback_eligible_error(_StatusError(status)) is False

    def test_unknown_status_is_not_eligible(self):
        assert is_fallback_eligible_error(Exception("network blip")) is False

    def test_status_on_response_attribute(self):
        assert is_fallback_eligible_error(_ResponseError(400)) is True
        assert is_fallback_eligible_error(_ResponseError(429)) is False
        assert is_fallback_eligible_error(_ResponseError(503)) is False
