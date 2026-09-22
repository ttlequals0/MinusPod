"""Tests for Ollama's native /api/chat path used to honor num_ctx (#780)."""
import unittest
from unittest.mock import MagicMock, patch

import requests

from llm_client import OllamaNativeChatError, extract_retry_after
from utils.llm_call import LOSS_SERVER_ERROR, window_loss_class


class TestGetEffectiveOllamaNumCtx(unittest.TestCase):
    """DB (via get_stage_tunable) resolution of the num_ctx setting."""

    @patch('llm_client._get_cached_setting', return_value=None)
    @patch.dict('os.environ', {}, clear=False)
    def test_returns_none_when_unset(self, _mock):
        import os
        os.environ.pop('OLLAMA_NUM_CTX', None)
        from llm_client import get_effective_ollama_num_ctx
        self.assertIsNone(get_effective_ollama_num_ctx())

    @patch('llm_client._get_cached_setting', return_value='32768')
    def test_returns_configured_value(self, _mock):
        from llm_client import get_effective_ollama_num_ctx
        self.assertEqual(get_effective_ollama_num_ctx(), 32768)

    @patch('llm_client._get_cached_setting', return_value='0')
    @patch.dict('os.environ', {}, clear=False)
    def test_zero_treated_as_unset(self, _mock):
        import os
        os.environ.pop('OLLAMA_NUM_CTX', None)
        from llm_client import get_effective_ollama_num_ctx
        self.assertIsNone(get_effective_ollama_num_ctx())


def _openai_style_response(content="openai response"):
    response = MagicMock()
    choice = MagicMock()
    choice.message.content = content
    choice.finish_reason = "stop"
    choice.message.reasoning = None
    choice.message.reasoning_content = None
    response.choices = [choice]
    response.usage = MagicMock(prompt_tokens=10, completion_tokens=5)
    response.model = "qwen3"
    return response


def _native_resp(status_code=200, json_body=None, text=""):
    resp = MagicMock()
    resp.ok = 200 <= status_code < 300
    resp.status_code = status_code
    resp.text = text
    resp.json.return_value = json_body or {}
    return resp


class TestOpenAICompatibleClientNativeOllamaChat(unittest.TestCase):
    def _make_client(self, num_ctx=None, api_key='not-needed'):
        from llm_client import OllamaNativeClient, OpenAICompatibleClient
        if num_ctx:
            client = OllamaNativeClient(
                base_url='http://localhost:11434/v1',
                api_key=api_key,
                default_model='qwen3',
                ollama_num_ctx=num_ctx,
            )
        else:
            client = OpenAICompatibleClient(
                base_url='http://localhost:11434/v1',
                api_key=api_key,
                default_model='qwen3',
            )
        client._token_param_cache.clear()
        client._client = MagicMock()
        return client

    def test_num_ctx_unset_uses_openai_path(self):
        client = self._make_client(num_ctx=None)
        client._client.chat.completions.create.return_value = _openai_style_response()

        with patch('utils.safe_http.safe_post') as mock_post:
            result = client.messages_create(
                model="qwen3", max_tokens=100, system="sys",
                messages=[{"role": "user", "content": "hi"}],
            )

        mock_post.assert_not_called()
        client._client.chat.completions.create.assert_called_once()
        self.assertEqual(result.content, "openai response")

    def test_num_ctx_set_posts_to_native_chat_with_options(self):
        client = self._make_client(num_ctx=32768)
        native = _native_resp(200, {
            "model": "qwen3",
            "message": {"role": "assistant", "content": "hello"},
            "done_reason": "stop",
            "prompt_eval_count": 120,
            "eval_count": 40,
        })

        with patch('utils.safe_http.safe_post', return_value=native) as mock_post:
            result = client.messages_create(
                model="qwen3", max_tokens=256, system="sys prompt",
                messages=[{"role": "user", "content": "hi"}],
                response_format={"type": "json_object"},
                reasoning_effort="high",
            )

        client._client.chat.completions.create.assert_not_called()
        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        self.assertEqual(args[0], 'http://localhost:11434/api/chat')
        body = kwargs['json']
        self.assertEqual(body['options']['num_ctx'], 32768)
        self.assertEqual(body['options']['num_predict'], 256)
        self.assertIs(body['stream'], False)
        self.assertEqual(body['messages'][0], {"role": "system", "content": "sys prompt"})
        self.assertEqual(body['format'], 'json')
        self.assertEqual(body['think'], 'high')
        self.assertEqual(result.content, "hello")
        self.assertEqual(result.usage, {'input_tokens': 120, 'output_tokens': 40})
        self.assertEqual(result.model, "qwen3")

    def test_format_absent_when_no_response_format_requested(self):
        client = self._make_client(num_ctx=8192)
        native = _native_resp(200, {
            "message": {"content": "ok"}, "done_reason": "stop",
            "prompt_eval_count": 1, "eval_count": 1,
        })

        with patch('utils.safe_http.safe_post', return_value=native) as mock_post:
            client.messages_create(
                model="qwen3", max_tokens=100, system="s",
                messages=[{"role": "user", "content": "hi"}],
            )

        body = mock_post.call_args.kwargs['json']
        self.assertNotIn('format', body)

    def test_json_schema_format_passes_raw_schema_object(self):
        client = self._make_client(num_ctx=8192)
        schema = {"type": "object", "properties": {"ads": {"type": "array"}}}
        native = _native_resp(200, {
            "message": {"content": "{}"}, "done_reason": "stop",
            "prompt_eval_count": 1, "eval_count": 1,
        })

        with patch('utils.safe_http.safe_post', return_value=native) as mock_post:
            client.messages_create(
                model="qwen3", max_tokens=100, system="s",
                messages=[{"role": "user", "content": "hi"}],
                response_format={"type": "json_schema", "json_schema": {"name": "x", "schema": schema}},
            )

        body = mock_post.call_args.kwargs['json']
        self.assertEqual(body['format'], schema)

    def test_think_false_when_reasoning_effort_none_or_omitted(self):
        client = self._make_client(num_ctx=8192)
        native = _native_resp(200, {
            "message": {"content": "ok"}, "done_reason": "stop",
            "prompt_eval_count": 1, "eval_count": 1,
        })

        with patch('utils.safe_http.safe_post', return_value=native) as mock_post:
            client.messages_create(
                model="qwen3", max_tokens=100, system="s",
                messages=[{"role": "user", "content": "hi"}],
                reasoning_effort=None,
            )
        self.assertIs(mock_post.call_args.kwargs['json']['think'], False)

        with patch('utils.safe_http.safe_post', return_value=native) as mock_post:
            client.messages_create(
                model="qwen3", max_tokens=100, system="s",
                messages=[{"role": "user", "content": "hi"}],
                reasoning_effort="none",
            )
        self.assertIs(mock_post.call_args.kwargs['json']['think'], False)

    def test_think_preserves_low_medium_high_levels(self):
        client = self._make_client(num_ctx=8192)
        native = _native_resp(200, {
            "message": {"content": "ok"}, "done_reason": "stop",
            "prompt_eval_count": 1, "eval_count": 1,
        })
        for level in ("low", "medium", "high"):
            with patch('utils.safe_http.safe_post', return_value=native) as mock_post:
                client.messages_create(
                    model="gpt-oss", max_tokens=100, system="s",
                    messages=[{"role": "user", "content": "hi"}],
                    reasoning_effort=level,
                )
            self.assertEqual(mock_post.call_args.kwargs['json']['think'], level)

    def test_think_false_for_none_and_omitted(self):
        client = self._make_client(num_ctx=8192)
        native = _native_resp(200, {
            "message": {"content": "ok"}, "done_reason": "stop",
            "prompt_eval_count": 1, "eval_count": 1,
        })
        for reasoning_effort in (None, "none"):
            with patch('utils.safe_http.safe_post', return_value=native) as mock_post:
                client.messages_create(
                    model="qwen3", max_tokens=100, system="s",
                    messages=[{"role": "user", "content": "hi"}],
                    reasoning_effort=reasoning_effort,
                )
            self.assertIs(mock_post.call_args.kwargs['json']['think'], False)

    def test_done_reason_length_sets_finish_reason_length(self):
        client = self._make_client(num_ctx=8192)
        native = _native_resp(200, {
            "message": {"content": "cut off"}, "done_reason": "length",
            "prompt_eval_count": 5, "eval_count": 100,
        })
        with patch('utils.safe_http.safe_post', return_value=native):
            result = client.messages_create(
                model="qwen3", max_tokens=100, system="s",
                messages=[{"role": "user", "content": "hi"}],
            )
        self.assertEqual(result.finish_reason, 'length')

    def test_500_classified_as_server_error_and_retryable(self):
        from llm_client import is_retryable_error
        client = self._make_client(num_ctx=8192)
        native = _native_resp(500, text="internal error")

        with patch('utils.safe_http.safe_post', return_value=native):
            with self.assertRaises(Exception) as ctx:
                client.messages_create(
                    model="qwen3", max_tokens=100, system="s",
                    messages=[{"role": "user", "content": "hi"}],
                )

        err = ctx.exception
        self.assertEqual(window_loss_class(err), LOSS_SERVER_ERROR)
        self.assertTrue(is_retryable_error(err))

    def test_connection_error_classified_as_connectivity(self):
        from llm_client import is_connectivity_error
        client = self._make_client(num_ctx=8192)

        with patch('utils.safe_http.safe_post',
                   side_effect=requests.exceptions.ConnectionError("boom")):
            with self.assertRaises(requests.exceptions.ConnectionError) as ctx:
                client.messages_create(
                    model="qwen3", max_tokens=100, system="s",
                    messages=[{"role": "user", "content": "hi"}],
                )

        self.assertTrue(is_connectivity_error(ctx.exception))

    def test_auth_header_sent_when_api_key_configured(self):
        client = self._make_client(num_ctx=8192, api_key='cloud-key')
        native = _native_resp(200, {
            "message": {"content": "ok"}, "done_reason": "stop",
            "prompt_eval_count": 1, "eval_count": 1,
        })
        with patch('utils.safe_http.safe_post', return_value=native) as mock_post:
            client.messages_create(
                model="qwen3", max_tokens=100, system="s",
                messages=[{"role": "user", "content": "hi"}],
            )
        headers = mock_post.call_args.kwargs['headers']
        self.assertEqual(headers['Authorization'], 'Bearer cloud-key')

    def test_no_auth_header_when_api_key_not_needed(self):
        client = self._make_client(num_ctx=8192, api_key='not-needed')
        native = _native_resp(200, {
            "message": {"content": "ok"}, "done_reason": "stop",
            "prompt_eval_count": 1, "eval_count": 1,
        })
        with patch('utils.safe_http.safe_post', return_value=native) as mock_post:
            client.messages_create(
                model="qwen3", max_tokens=100, system="s",
                messages=[{"role": "user", "content": "hi"}],
            )
        self.assertIsNone(mock_post.call_args.kwargs['headers'])

    def test_rate_limit_error_preserves_response_headers(self):
        client = self._make_client(num_ctx=8192)
        native = _native_resp(429, text="busy")
        native.headers = {"Retry-After": "120"}

        with patch('utils.safe_http.safe_post', return_value=native):
            with self.assertRaises(Exception) as ctx:
                client.messages_create(
                    model="qwen3", max_tokens=100, system="s",
                    messages=[{"role": "user", "content": "hi"}],
                )

        self.assertIsInstance(ctx.exception, OllamaNativeChatError)
        self.assertEqual(extract_retry_after(ctx.exception), 120.0)


class TestBuildClientOllamaNumCtx(unittest.TestCase):
    """The factory only wires num_ctx into clients built for the Ollama provider."""

    @patch('llm_client._get_cached_secret', return_value=None)
    @patch('llm_client.get_effective_ollama_num_ctx', return_value=32768)
    def test_ollama_provider_gets_num_ctx(self, _mock_ctx, _mock_secret):
        from llm_client import _build_client, OllamaNativeClient, PROVIDER_OLLAMA
        client = _build_client(PROVIDER_OLLAMA, base_url='http://localhost:11434/v1')
        self.assertIsInstance(client, OllamaNativeClient)
        self.assertEqual(client._ollama_num_ctx, 32768)

    @patch('llm_client._get_cached_secret', return_value=None)
    @patch('llm_client.get_effective_ollama_num_ctx', return_value=32768)
    def test_openai_compatible_provider_does_not_get_num_ctx(self, _mock_ctx, _mock_secret):
        from llm_client import _build_client, OllamaNativeClient, PROVIDER_OPENAI_COMPATIBLE
        client = _build_client(PROVIDER_OPENAI_COMPATIBLE, base_url='http://localhost:8000/v1')
        self.assertNotIsInstance(client, OllamaNativeClient)


if __name__ == '__main__':
    unittest.main()
