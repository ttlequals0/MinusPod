"""Issues #693/#694: json_schema response_format for OpenAI-compatible
providers. Covers the provider-level probe, the runtime gate, the
json_object downgrade in _build_kwargs, and the runtime-400 retry.

Uses fresh Database() instances (not the main_app.db singleton) for settings
writes: llm_client's internals read settings through Database() too, so the
two stay in the same data dir regardless of module import order."""
from unittest.mock import MagicMock

from tests.app_bootstrap import bootstrap
bootstrap('json_schema_capability_test_')

from database import Database
from llm_client import (
    OpenAICompatibleClient, _rejects_json_mode, supports_json_schema_for_calls,
)
from utils.llm_call import call_llm_for_window


def _db():
    return Database()


def _make_client():
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


def _invalidate():
    import llm_client
    llm_client.invalidate_provider_cache()


class TestRejectsJsonSchema:
    def test_schema_wording_detected(self):
        assert _rejects_json_mode("400 json_schema is not supported")
        assert _rejects_json_mode("400 response_format json_schema unsupported")

    def test_unrelated_400_not_detected(self):
        assert not _rejects_json_mode("400 max_tokens is too large")


class TestProbe:
    def test_success_persists_true(self):
        client = _make_client()
        client._client.chat.completions.create.return_value = _mock_response()
        result = client.probe_json_schema_support(model='test-model')
        assert result is True
        assert client._json_schema_supported is True
        assert _db().get_setting('llm_json_schema_supported') == 'true'
        _db().clear_setting('llm_json_schema_supported')

    def test_rejection_persists_false(self):
        client = _make_client()
        client._client.chat.completions.create.side_effect = _bad_request_error(
            "400 json_schema is not supported")
        result = client.probe_json_schema_support(model='test-model')
        assert result is False
        assert _db().get_setting('llm_json_schema_supported') == 'false'
        _db().clear_setting('llm_json_schema_supported')

    def test_unexpected_400_inconclusive(self):
        client = _make_client()
        client._client.chat.completions.create.side_effect = _bad_request_error(
            "400 max_tokens too large")
        assert client.probe_json_schema_support(model='test-model') is None


class TestBuildKwargsDowngrade:
    def test_json_schema_downgrades_to_json_object_when_unsupported(self):
        client = _make_client()
        client._json_schema_supported = False
        client._json_format_supported = True
        client._client.chat.completions.create.return_value = _mock_response()
        client.messages_create(
            model="test-model", max_tokens=100, system="sys",
            messages=[{"role": "user", "content": "hi"}],
            response_format={"type": "json_schema", "json_schema": {"name": "x"}},
        )
        # Direct create path (cached token param) receives the downgraded format.
        sent = client._client.chat.completions.create.call_args_list[-1][1]
        assert sent['response_format'] == {"type": "json_object"}

    def test_json_schema_sent_verbatim_when_supported(self):
        client = _make_client()
        client._json_schema_supported = True
        client._json_format_supported = True
        client._client.chat.completions.create.return_value = _mock_response()
        rf = {"type": "json_schema", "json_schema": {"name": "x"}}
        client.messages_create(
            model="test-model", max_tokens=100, system="sys",
            messages=[{"role": "user", "content": "hi"}],
            response_format=rf,
        )
        sent = client._client.chat.completions.create.call_args_list[-1][1]
        assert sent['response_format'] is rf


class TestRuntime400Retry:
    def test_schema_rejection_downgrades_and_persists(self):
        client = _make_client()
        client._token_param_cache['test-model'] = 'max_completion_tokens'
        client._json_schema_supported = None
        client._json_format_supported = True
        error = _bad_request_error("400 json_schema is not supported")
        client._client.chat.completions.create.side_effect = [error, _mock_response("recovered")]

        result = client.messages_create(
            model="test-model", max_tokens=100, system="sys",
            messages=[{"role": "user", "content": "hi"}],
            response_format={"type": "json_schema", "json_schema": {"name": "x"}},
        )
        assert result.content == "recovered"
        assert client._json_schema_supported is False
        second = client._client.chat.completions.create.call_args_list[1][1]
        assert second['response_format'] == {"type": "json_object"}


class TestSupportsJsonSchemaForCalls:
    def _configure(self, provider, enabled=None, probed=None):
        if provider is None:
            _db().clear_setting('llm_provider')
        else:
            _db().set_setting('llm_provider', provider)
        if enabled is None:
            _db().clear_setting('llm_json_schema_enabled')
        else:
            _db().set_setting('llm_json_schema_enabled', 'true' if enabled else 'false')
        if probed is None:
            _db().clear_setting('llm_json_schema_supported')
        else:
            _db().set_setting('llm_json_schema_supported', 'true' if probed else 'false')
        _invalidate()

    def setup_method(self):
        self._previous = (
            _db().get_setting('llm_provider'),
            _db().get_setting('llm_json_schema_enabled'),
            _db().get_setting('llm_json_schema_supported'),
        )

    def teardown_method(self):
        provider, enabled, probed = self._previous
        if provider is None:
            _db().clear_setting('llm_provider')
        else:
            _db().set_setting('llm_provider', provider)
        if enabled is None:
            _db().clear_setting('llm_json_schema_enabled')
        else:
            _db().set_setting('llm_json_schema_enabled', enabled)
        if probed is None:
            _db().clear_setting('llm_json_schema_supported')
        else:
            _db().set_setting('llm_json_schema_supported', probed)
        _invalidate()

    def test_false_for_anthropic_even_when_enabled_and_probed(self):
        self._configure('anthropic', enabled=True, probed=True)
        assert supports_json_schema_for_calls() is False

    def test_true_for_openai_compatible_when_opted_and_probed(self):
        self._configure('openai-compatible', enabled=True, probed=True)
        assert supports_json_schema_for_calls() is True

    def test_false_when_not_opted_in(self):
        self._configure('openai-compatible', enabled=False, probed=True)
        assert supports_json_schema_for_calls() is False

    def test_false_when_probe_not_passed(self):
        self._configure('openai-compatible', enabled=True, probed=None)
        assert supports_json_schema_for_calls() is False


class TestCallLlmForWindowFormat:
    def test_default_json_object(self, monkeypatch):
        captured = {}
        import utils.llm_call as lc
        monkeypatch.setattr(lc, 'call_llm', lambda **kw: captured.update(kw) or (None, None))
        call_llm_for_window(window_label='w', llm_client=object(), model='m')
        assert captured['response_format'] == {"type": "json_object"}

    def test_explicit_schema_passthrough(self, monkeypatch):
        captured = {}
        import utils.llm_call as lc
        monkeypatch.setattr(lc, 'call_llm', lambda **kw: captured.update(kw) or (None, None))
        rf = {"type": "json_schema", "json_schema": {"name": "x"}}
        call_llm_for_window(window_label='w', response_format=rf, llm_client=object())
        assert captured['response_format'] is rf
