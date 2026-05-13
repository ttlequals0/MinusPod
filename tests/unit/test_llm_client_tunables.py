"""Tests for messages_create extensions: reasoning_effort + per-pass fallback."""
from unittest.mock import MagicMock, patch

import pytest

import llm_capabilities
from llm_capabilities import (
    PASS_AD_DETECTION_1,
    PASS_REVIEWER_1,
    clear_fallback,
    is_fallback_set,
)


@pytest.fixture(autouse=True)
def _reset_state():
    with llm_capabilities._fallback_lock:
        llm_capabilities._fallback_state.clear()


def _make_anthropic_response(text="ok"):
    response = MagicMock()
    response.content = [MagicMock(text=text)]
    response.usage = MagicMock(input_tokens=10, output_tokens=5)
    response.stop_reason = "end_turn"
    return response


def _make_openai_response(content="ok", finish_reason="stop"):
    response = MagicMock()
    choice = MagicMock()
    choice.message.content = content
    choice.finish_reason = finish_reason
    choice.message.reasoning = None
    choice.message.reasoning_content = None
    response.choices = [choice]
    response.usage = MagicMock(prompt_tokens=10, completion_tokens=5)
    return response


class _FakeAPIError(Exception):
    def __init__(self, status_code, message="rejected"):
        super().__init__(message)
        self.status_code = status_code


class TestAnthropicReasoningTranslation:
    def test_anthropic_passes_thinking_block_when_budget_set(self):
        from llm_client import AnthropicClient
        client = AnthropicClient(api_key="dummy")
        mock_sdk = MagicMock()
        mock_sdk.messages.create.return_value = _make_anthropic_response()
        client._client = mock_sdk

        client.messages_create(
            model="claude-opus-4-7",
            max_tokens=4096,
            system="sys",
            messages=[{"role": "user", "content": "hi"}],
            reasoning_effort=8192,
        )

        kwargs = mock_sdk.messages.create.call_args.kwargs
        assert kwargs["thinking"] == {"type": "enabled", "budget_tokens": 8192}

    def test_anthropic_omits_thinking_when_reasoning_none(self):
        from llm_client import AnthropicClient
        client = AnthropicClient(api_key="dummy")
        mock_sdk = MagicMock()
        mock_sdk.messages.create.return_value = _make_anthropic_response()
        client._client = mock_sdk

        client.messages_create(
            model="claude-opus-4-7",
            max_tokens=4096,
            system="sys",
            messages=[{"role": "user", "content": "hi"}],
            reasoning_effort=None,
        )

        kwargs = mock_sdk.messages.create.call_args.kwargs
        assert "thinking" not in kwargs


class TestAnthropicFallback:
    def _build_client(self):
        from llm_client import AnthropicClient
        client = AnthropicClient(api_key="dummy")
        return client

    def test_4xx_flips_flag_and_retries_with_defaults(self):
        client = self._build_client()
        mock_sdk = MagicMock()
        # First call raises 400; second call succeeds.
        mock_sdk.messages.create.side_effect = [
            _FakeAPIError(400, "max_tokens too large"),
            _make_anthropic_response(),
        ]
        client._client = mock_sdk

        client.messages_create(
            model="claude-x",
            max_tokens=99999,
            system="sys",
            messages=[{"role": "user", "content": "hi"}],
            temperature=0.7,
            episode_id="ep1",
            pass_name=PASS_AD_DETECTION_1,
        )

        assert is_fallback_set("ep1", PASS_AD_DETECTION_1) is True
        assert mock_sdk.messages.create.call_count == 2
        retry_kwargs = mock_sdk.messages.create.call_args_list[1].kwargs
        assert retry_kwargs["max_tokens"] == 4096  # default for ad_detection_pass_1
        assert retry_kwargs["temperature"] == 0.0

    def test_429_does_not_flip_flag(self):
        client = self._build_client()
        mock_sdk = MagicMock()
        mock_sdk.messages.create.side_effect = _FakeAPIError(429, "rate limited")
        client._client = mock_sdk

        with pytest.raises(_FakeAPIError):
            client.messages_create(
                model="claude-x",
                max_tokens=4096,
                system="sys",
                messages=[{"role": "user", "content": "hi"}],
                episode_id="ep1",
                pass_name=PASS_AD_DETECTION_1,
            )

        assert is_fallback_set("ep1", PASS_AD_DETECTION_1) is False
        assert mock_sdk.messages.create.call_count == 1  # No retry on rate limit.

    def test_5xx_does_not_flip_flag(self):
        client = self._build_client()
        mock_sdk = MagicMock()
        mock_sdk.messages.create.side_effect = _FakeAPIError(503, "upstream down")
        client._client = mock_sdk

        with pytest.raises(_FakeAPIError):
            client.messages_create(
                model="claude-x",
                max_tokens=4096,
                system="sys",
                messages=[{"role": "user", "content": "hi"}],
                episode_id="ep1",
                pass_name=PASS_AD_DETECTION_1,
            )

        assert is_fallback_set("ep1", PASS_AD_DETECTION_1) is False

    def test_no_pass_name_no_fallback(self):
        client = self._build_client()
        mock_sdk = MagicMock()
        mock_sdk.messages.create.side_effect = _FakeAPIError(400, "bad")
        client._client = mock_sdk

        with pytest.raises(_FakeAPIError):
            client.messages_create(
                model="claude-x",
                max_tokens=4096,
                system="sys",
                messages=[{"role": "user", "content": "hi"}],
            )

        assert mock_sdk.messages.create.call_count == 1

    def test_already_in_fallback_uses_defaults_no_retry(self):
        client = self._build_client()
        mock_sdk = MagicMock()
        mock_sdk.messages.create.return_value = _make_anthropic_response()
        client._client = mock_sdk

        llm_capabilities.set_fallback("ep1", PASS_AD_DETECTION_1)

        client.messages_create(
            model="claude-x",
            max_tokens=99999,
            system="sys",
            messages=[{"role": "user", "content": "hi"}],
            temperature=0.9,
            episode_id="ep1",
            pass_name=PASS_AD_DETECTION_1,
        )

        assert mock_sdk.messages.create.call_count == 1
        kwargs = mock_sdk.messages.create.call_args.kwargs
        # Defaults are used directly on first attempt.
        assert kwargs["max_tokens"] == 4096
        assert kwargs["temperature"] == 0.0

    def test_4xx_does_not_trip_circuit_breaker(self):
        # Per-pass fallback is a user-config retry, not a provider outage.
        client = self._build_client()
        mock_sdk = MagicMock()
        mock_sdk.messages.create.side_effect = [
            _FakeAPIError(400, "bad"),
            _make_anthropic_response(),
        ]
        client._client = mock_sdk
        with patch.object(client, "_record_circuit_breaker") as mock_record:
            client.messages_create(
                model="claude-x", max_tokens=99999, system="sys",
                messages=[{"role": "user", "content": "hi"}],
                episode_id="ep1", pass_name=PASS_AD_DETECTION_1,
            )
            # Only the final success records the breaker -- no failure on the 4xx.
            calls = [c.kwargs for c in mock_record.call_args_list]
            assert {"success": True} in calls
            assert {"success": False} not in calls

    def test_episode_scoping(self):
        client = self._build_client()
        mock_sdk = MagicMock()
        # ep1 hits 400 + retries; ep2 happens later, uses user tunables.
        mock_sdk.messages.create.side_effect = [
            _FakeAPIError(400, "bad"),
            _make_anthropic_response(),
            _make_anthropic_response(),  # ep2 call
        ]
        client._client = mock_sdk

        client.messages_create(
            model="claude-x", max_tokens=99999, system="sys",
            messages=[{"role": "user", "content": "hi"}],
            temperature=0.9, episode_id="ep1", pass_name=PASS_AD_DETECTION_1,
        )
        client.messages_create(
            model="claude-x", max_tokens=99999, system="sys",
            messages=[{"role": "user", "content": "hi"}],
            temperature=0.9, episode_id="ep2", pass_name=PASS_AD_DETECTION_1,
        )

        assert is_fallback_set("ep1", PASS_AD_DETECTION_1) is True
        assert is_fallback_set("ep2", PASS_AD_DETECTION_1) is False
        # ep2 call still used user tunables.
        ep2_kwargs = mock_sdk.messages.create.call_args_list[2].kwargs
        assert ep2_kwargs["max_tokens"] == 99999
        assert ep2_kwargs["temperature"] == 0.9


class TestOpenAIFallback:
    def _build_client(self, provider="openai-compatible"):
        from llm_client import OpenAICompatibleClient
        client = OpenAICompatibleClient(api_key="dummy", base_url="http://x/v1")
        # Force token_param cache so the wrapper path isn't taken (simpler mock).
        client._token_param_cache["model-x"] = "max_tokens"
        return client

    def test_4xx_flips_flag_and_retries(self):
        client = self._build_client()
        mock_sdk = MagicMock()
        mock_sdk.chat.completions.create.side_effect = [
            _FakeAPIError(400, "max_tokens too large"),
            _make_openai_response(),
        ]
        client._client = mock_sdk

        with patch("llm_client.get_effective_provider", return_value="openai-compatible"):
            client.messages_create(
                model="model-x",
                max_tokens=99999,
                system="sys",
                messages=[{"role": "user", "content": "hi"}],
                temperature=0.8,
                episode_id="ep1",
                pass_name=PASS_REVIEWER_1,
            )

        assert is_fallback_set("ep1", PASS_REVIEWER_1) is True
        assert mock_sdk.chat.completions.create.call_count == 2
        retry_kwargs = mock_sdk.chat.completions.create.call_args_list[1].kwargs
        assert retry_kwargs["max_tokens"] == 4096  # default for reviewer
        assert retry_kwargs["temperature"] == 0.0

    def test_reasoning_translation_openrouter(self):
        client = self._build_client()
        mock_sdk = MagicMock()
        mock_sdk.chat.completions.create.return_value = _make_openai_response()
        client._client = mock_sdk

        with patch("llm_client.get_effective_provider", return_value="openrouter"):
            client.messages_create(
                model="model-x",
                max_tokens=4096,
                system="sys",
                messages=[{"role": "user", "content": "hi"}],
                reasoning_effort="high",
            )

        kwargs = mock_sdk.chat.completions.create.call_args.kwargs
        assert kwargs["extra_body"] == {"reasoning": {"effort": "high"}}

    def test_reasoning_translation_ollama_none(self):
        client = self._build_client()
        mock_sdk = MagicMock()
        mock_sdk.chat.completions.create.return_value = _make_openai_response()
        client._client = mock_sdk

        with patch("llm_client.get_effective_provider", return_value="ollama"):
            client.messages_create(
                model="model-x",
                max_tokens=4096,
                system="sys",
                messages=[{"role": "user", "content": "hi"}],
                reasoning_effort="none",
            )

        kwargs = mock_sdk.chat.completions.create.call_args.kwargs
        assert kwargs["extra_body"] == {"options": {"think": False}}
