"""Tests for JSON-mode rejection detection and runtime self-correction."""
from unittest.mock import MagicMock

from tests.app_bootstrap import bootstrap
bootstrap('json_mode_probe_test_')

from llm_client import _rejects_json_mode


def test_novita_wording_detected():
    assert _rejects_json_mode(
        "Error code: 400 - model X does not support feature: structured-outputs")


def test_classic_wording_detected():
    assert _rejects_json_mode("400 response_format is not supported")


def test_unrelated_400_not_detected():
    assert not _rejects_json_mode("400 max_tokens is too large")


def _make_client():
    from llm_client import OpenAICompatibleClient
    client = OpenAICompatibleClient(
        base_url='http://localhost:8000/v1',
        api_key='test-key',
        default_model='test-model'
    )
    client._token_param_cache.clear()
    client._client = MagicMock()
    return client


def _bad_request_error(message):
    from openai import BadRequestError
    error_body = {'error': {'message': message}}
    return BadRequestError(
        message=message,
        response=MagicMock(status_code=400, json=lambda: error_body,
                            headers={}, text=""),
        body=error_body,
    )


def _mock_response(text="ok"):
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = text
    mock_response.usage = MagicMock(prompt_tokens=10, completion_tokens=5)
    mock_response.model = "test-model"
    return mock_response


class TestRuntimeJsonModeFallback:
    def test_retries_once_and_disables_response_format(self):
        client = _make_client()
        client._token_param_cache['test-model'] = 'max_completion_tokens'
        error = _bad_request_error(
            "Error code: 400 - model X does not support feature: structured-outputs")
        client._client.chat.completions.create.side_effect = [error, _mock_response("recovered")]

        result = client.messages_create(
            model="test-model", max_tokens=100, system="test system",
            messages=[{"role": "user", "content": "hi"}],
            response_format={"type": "json_object"},
        )

        assert result.content == "recovered"
        assert client._json_format_supported is False
        assert client._client.chat.completions.create.call_count == 2
        second_kwargs = client._client.chat.completions.create.call_args_list[1][1]
        assert 'response_format' not in second_kwargs
        injected = second_kwargs['messages'][0]['content']
        assert injected.count('<output_format>') == 1

    def test_unrelated_400_unprobed_endpoint_retry_fails_reraises_original(self):
        """Unprobed endpoint (flag None): the unrecognized wording still triggers
        a speculative retry, but when that retry fails with the same 400 the
        original error must propagate and nothing gets persisted."""
        client = _make_client()
        client._token_param_cache['test-model'] = 'max_completion_tokens'
        error = _bad_request_error("400 max_tokens is too large")
        client._client.chat.completions.create.side_effect = [error, error]
        client._persist_json_format_flag = MagicMock()

        try:
            client.messages_create(
                model="test-model", max_tokens=100, system="test system",
                messages=[{"role": "user", "content": "hi"}],
                response_format={"type": "json_object"},
            )
            assert False, "expected BadRequestError to propagate"
        except Exception as e:
            assert "max_tokens is too large" in str(e)
        assert client._client.chat.completions.create.call_count == 2
        assert client._json_format_supported is None
        client._persist_json_format_flag.assert_not_called()

    def test_unknown_phrasing_unprobed_endpoint_retry_succeeds_persists(self):
        """Unrecognized wording, unprobed endpoint: speculative retry succeeds,
        so the fallback is confirmed and persisted."""
        client = _make_client()
        client._token_param_cache['test-model'] = 'max_completion_tokens'
        error = _bad_request_error("400 output schema mode is not available for this model")
        client._client.chat.completions.create.side_effect = [error, _mock_response("recovered")]
        client._persist_json_format_flag = MagicMock()

        result = client.messages_create(
            model="test-model", max_tokens=100, system="test system",
            messages=[{"role": "user", "content": "hi"}],
            response_format={"type": "json_object"},
        )

        assert result.content == "recovered"
        assert client._json_format_supported is False
        client._persist_json_format_flag.assert_called_once()
        assert client._client.chat.completions.create.call_count == 2
        second_kwargs = client._client.chat.completions.create.call_args_list[1][1]
        assert 'response_format' not in second_kwargs
        injected = second_kwargs['messages'][0]['content']
        assert injected.count('<output_format>') == 1

    def test_token_param_fallback_not_shadowed_for_uncached_model(self):
        """Uncached model: a token-param 400 must still reach
        _call_with_token_param_fallback rather than the json-mode handler."""
        client = _make_client()
        error = _bad_request_error("Unsupported parameter: 'max_completion_tokens'")
        client._client.chat.completions.create.side_effect = [error, _mock_response("ok")]

        result = client.messages_create(
            model="test-model", max_tokens=100, system="test system",
            messages=[{"role": "user", "content": "hi"}],
        )

        assert result.content == "ok"
        assert client._token_param_cache['test-model'] == 'max_tokens'
        second_kwargs = client._client.chat.completions.create.call_args_list[1][1]
        assert 'max_tokens' in second_kwargs
