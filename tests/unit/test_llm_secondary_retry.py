"""Provider error classification after the primary LLM retry loop."""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from llm_client import (
    OpenAICompatibleClient,
    ProviderRateLimitedError,
    StructuralRateLimitError,
)
from tests.unit.provider_error_fakes import FakeProviderError, FakeResponse, call_window
from utils import llm_call


class _SequenceClient:
    def __init__(self, *results):
        self.results = results
        self.calls = 0

    def messages_create(self, **kwargs):
        result = self.results[min(self.calls, len(self.results) - 1)]
        self.calls += 1
        if isinstance(result, Exception):
            raise result
        return result


@pytest.fixture
def no_retry_wait(monkeypatch):
    sleeps = []
    monkeypatch.setattr(llm_call.time, 'sleep', sleeps.append)
    return sleeps


def _secondary_result(client):
    return call_window(client, max_retries=0)


def test_persistent_empty_completion_keeps_six_attempts(no_retry_wait):
    client = _SequenceClient(SimpleNamespace(content=''))

    response, error = call_window(client, max_retries=3)

    assert response is None
    assert isinstance(error, llm_call.EmptyCompletionError)
    assert client.calls == 6


def test_secondary_retry_applies_provider_hold(monkeypatch, no_retry_wait):
    monkeypatch.setattr(llm_call, 'is_rate_limit_hold_enabled', lambda: True)
    transient = FakeProviderError('503 unavailable', status_code=503)
    rate_limit = FakeProviderError(
        '429 rate limit', status_code=429,
        response=FakeResponse(headers={'Retry-After': '600'}),
    )
    client = _SequenceClient(transient, rate_limit)

    response, error = _secondary_result(client)

    assert response is None
    assert isinstance(error, ProviderRateLimitedError)
    assert error.retry_after_seconds == 600.0
    assert client.calls == 2
    assert no_retry_wait == [2]


def test_secondary_retry_applies_structural_limit(monkeypatch, no_retry_wait):
    fired = MagicMock()
    monkeypatch.setattr('webhook_service.fire_structural_rate_limit_event', fired)
    transient = FakeProviderError('503 unavailable', status_code=503)
    structural = FakeProviderError('429 rate limit', status_code=429, body={
        'error': {
            'message': 'tokens per minute: Limit 6000, Used 0, Requested ~7500',
            'type': 'tokens',
            'code': 'rate_limit_exceeded',
        },
    })
    client = _SequenceClient(transient, structural)

    response, error = _secondary_result(client)

    assert response is None
    assert isinstance(error, StructuralRateLimitError)
    assert client.calls == 2
    assert no_retry_wait == [2]
    fired.assert_called_once()


def test_secondary_retry_applies_daily_quota(no_retry_wait):
    transient = FakeProviderError('503 unavailable', status_code=503)
    daily_quota = FakeProviderError('429 rate limit', status_code=429, body={
        'error': {
            'status': 'RESOURCE_EXHAUSTED',
            'details': [{
                '@type': 'type.googleapis.com/google.rpc.QuotaFailure',
                'violations': [{
                    'quotaMetric': 'generate_content_free_tier_requests',
                    'quotaId': 'GenerateRequestsPerDayPerProjectPerModel-FreeTier',
                    'quotaValue': '20',
                }],
            }],
        },
    })
    client = _SequenceClient(transient, daily_quota)

    response, error = _secondary_result(client)

    assert response is None
    assert isinstance(error, StructuralRateLimitError)
    assert 'daily quota' in str(error)
    assert client.calls == 2
    assert no_retry_wait == [2]


def test_secondary_retry_applies_spend_limit(monkeypatch, no_retry_wait):
    fired = MagicMock()
    monkeypatch.setattr('webhook_service.fire_limit_exceeded_event', fired)
    transient = FakeProviderError('503 unavailable', status_code=503)
    spend_limit = FakeProviderError(
        '403 Key limit exceeded (monthly limit)', status_code=403)
    client = _SequenceClient(transient, spend_limit)

    response, error = _secondary_result(client)

    assert response is None
    assert error is spend_limit
    assert client.calls == 2
    assert no_retry_wait == [2]
    fired.assert_called_once()


def test_secondary_retry_applies_auth_failure(monkeypatch, no_retry_wait):
    fired = MagicMock()
    monkeypatch.setattr('webhook_service.fire_auth_failure_event', fired)
    transient = FakeProviderError('503 unavailable', status_code=503)
    auth = FakeProviderError('401 invalid API key', status_code=401)
    client = _SequenceClient(transient, auth)

    response, error = _secondary_result(client)

    assert response is None
    assert error is auth
    assert client.calls == 2
    assert no_retry_wait == [2]
    fired.assert_called_once()


def test_secondary_auth_webhook_failure_does_not_escape(monkeypatch, no_retry_wait):
    monkeypatch.setattr(
        'webhook_service.fire_auth_failure_event',
        MagicMock(side_effect=RuntimeError('webhook unavailable')),
    )
    transient = FakeProviderError('503 unavailable', status_code=503)
    auth = FakeProviderError('401 invalid API key', status_code=401)
    client = _SequenceClient(transient, auth)

    response, error = _secondary_result(client)

    assert response is None
    assert error is auth
    assert client.calls == 2
    assert no_retry_wait == [2]


def test_empty_completion_records_usage_before_rejection(monkeypatch):
    provider_response = SimpleNamespace(
        choices=[SimpleNamespace(
            message=SimpleNamespace(
                content='', reasoning=None, reasoning_content=None),
            finish_reason='stop',
        )],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=4),
    )
    sdk = MagicMock()
    sdk.chat.completions.create.return_value = provider_response
    client = OpenAICompatibleClient(api_key='test-key')
    client._client = sdk
    usage_callback = MagicMock()
    client.set_usage_callback(usage_callback)
    monkeypatch.setattr('llm_client.get_effective_provider', lambda: 'openrouter')

    kwargs = {
        'model': 'test-model',
        'max_tokens': 100,
        'system': 'system',
        'messages': [{'role': 'user', 'content': 'prompt'}],
        'timeout': 1.0,
        'response_format': None,
        'reasoning_effort': None,
        'episode_id': None,
        'pass_name': None,
    }
    with pytest.raises(llm_call.EmptyCompletionError):
        llm_call._call_once(client, kwargs, 'test-model')

    usage_callback.assert_called_once_with(
        'test-model', {'input_tokens': 10, 'output_tokens': 4})
