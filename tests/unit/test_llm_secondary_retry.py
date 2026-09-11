"""Provider error classification after the primary LLM retry loop."""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import run_context
from llm_client import (
    AnthropicClient,
    OpenAICompatibleClient,
    ProviderRateLimitedError,
    StructuralRateLimitError,
)
from llm_capabilities import (
    PASS_AD_DETECTION_1,
    classify_reasoning_rejection,
    clear_fallback,
    is_fallback_set,
)
from tests.unit.provider_error_fakes import FakeProviderError, FakeResponse, call_window
from utils import llm_call


class _SequenceClient:
    def __init__(self, *results):
        self.results = results
        self.calls = 0
        self.call_kwargs = []

    def messages_create(self, **kwargs):
        self.call_kwargs.append(kwargs)
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
    client = _SequenceClient(SimpleNamespace(
        content='', reasoning_present=True, finish_reason='stop'))

    response, error = call_window(client, max_retries=3)

    assert response is None
    assert isinstance(error, llm_call.EmptyCompletionError)
    assert client.calls == 6
    assert all(call['reasoning_effort'] is None for call in client.call_kwargs)


@pytest.mark.parametrize('provider', ['openai-compatible', 'openrouter', 'ollama'])
def test_reasoning_exhaustion_retry_disables_reasoning_and_records_usage(
        provider, monkeypatch, no_retry_wait):
    exhausted = SimpleNamespace(
        choices=[SimpleNamespace(
            message=SimpleNamespace(
                content='', reasoning=None, reasoning_content=None,
                reasoning_details=None),
            finish_reason='length',
        )],
        usage=SimpleNamespace(
            prompt_tokens=100,
            completion_tokens=8192,
            completion_tokens_details=SimpleNamespace(reasoning_tokens=8192),
        ),
    )
    answered = SimpleNamespace(
        choices=[SimpleNamespace(
            message=SimpleNamespace(
                content='[]', reasoning=None, reasoning_content=None),
            finish_reason='stop',
        )],
        usage=SimpleNamespace(prompt_tokens=100, completion_tokens=2),
    )
    sdk = MagicMock()
    sdk.chat.completions.create.side_effect = [exhausted, answered]
    client = OpenAICompatibleClient(api_key='test-key')
    client._client = sdk
    client._token_param_cache['test-model'] = 'max_completion_tokens'
    usage_callback = MagicMock()
    client.set_usage_callback(usage_callback)
    monkeypatch.setattr('llm_client.get_effective_provider', lambda: provider)

    response, error = llm_call.call_llm_for_window(
        llm_client=client,
        model='test-model',
        system_prompt='sys',
        prompt='user',
        llm_timeout=1.0,
        max_retries=1,
        max_tokens=8192,
        slug='t',
        episode_id='e',
        window_label='w',
        reasoning_effort='high',
    )

    assert error is None
    assert response.content == '[]'
    first, second = sdk.chat.completions.create.call_args_list
    reasoning_key = 'extra_body' if provider == 'openrouter' else 'reasoning_effort'
    if provider == 'openrouter':
        assert first.kwargs[reasoning_key] == {'reasoning': {'effort': 'high'}}
        assert second.kwargs[reasoning_key] == {'reasoning': {'effort': 'none'}}
    else:
        assert first.kwargs[reasoning_key] == 'high'
        assert second.kwargs[reasoning_key] == 'none'
    assert {
        key: value for key, value in first.kwargs.items()
        if key != reasoning_key
    } == {
        key: value for key, value in second.kwargs.items()
        if key != reasoning_key
    }
    assert [item.args for item in usage_callback.call_args_list] == [
        ('test-model', {'input_tokens': 100, 'output_tokens': 8192}),
        ('test-model', {'input_tokens': 100, 'output_tokens': 2}),
    ]
    assert len(no_retry_wait) == 1


def test_anthropic_reasoning_exhaustion_retry_omits_thinking(
        monkeypatch, no_retry_wait):
    exhausted = SimpleNamespace(
        content=[SimpleNamespace(type='thinking', thinking='hidden reasoning')],
        stop_reason='max_tokens',
        usage=SimpleNamespace(input_tokens=100, output_tokens=4096),
    )
    answered = SimpleNamespace(
        content=[SimpleNamespace(type='text', text='[]')],
        stop_reason='end_turn',
        usage=SimpleNamespace(input_tokens=100, output_tokens=2),
    )
    sdk = MagicMock()
    sdk.messages.create.side_effect = [exhausted, answered]
    client = AnthropicClient(api_key='test-key')
    client._client = sdk
    usage_callback = MagicMock()
    client.set_usage_callback(usage_callback)

    response, error = llm_call.call_llm_for_window(
        llm_client=client,
        model='claude-test',
        system_prompt='sys',
        prompt='user',
        llm_timeout=1.0,
        max_retries=1,
        max_tokens=4096,
        slug='t',
        episode_id='e',
        window_label='w',
        reasoning_effort=2048,
    )

    assert error is None
    assert response.content == '[]'
    first, second = sdk.messages.create.call_args_list
    assert first.kwargs['thinking'] == {
        'type': 'enabled', 'budget_tokens': 2048,
    }
    assert 'thinking' not in second.kwargs
    assert [item.args for item in usage_callback.call_args_list] == [
        ('claude-test', {'input_tokens': 100, 'output_tokens': 4096}),
        ('claude-test', {'input_tokens': 100, 'output_tokens': 2}),
    ]
    assert len(no_retry_wait) == 1


def test_reasoning_none_rejection_uses_pass_fallback_after_exhaustion(
        monkeypatch, no_retry_wait, caplog):
    episode_id = 'reasoning-fallback'
    clear_fallback(episode_id, PASS_AD_DETECTION_1)
    exhausted = SimpleNamespace(
        choices=[SimpleNamespace(
            message=SimpleNamespace(
                content='', reasoning=None, reasoning_content=None,
                reasoning_details=None),
            finish_reason='length',
        )],
        usage=SimpleNamespace(
            prompt_tokens=100,
            completion_tokens=8192,
            completion_tokens_details=SimpleNamespace(reasoning_tokens=8192),
        ),
    )
    answered = SimpleNamespace(
        choices=[SimpleNamespace(
            message=SimpleNamespace(
                content='[]', reasoning=None, reasoning_content=None),
            finish_reason='stop',
        )],
        usage=SimpleNamespace(prompt_tokens=100, completion_tokens=2),
    )
    sdk = MagicMock()
    sdk.chat.completions.create.side_effect = [
        exhausted,
        FakeProviderError(
            'reasoning is required; private-provider-detail', status_code=400),
        answered,
    ]
    client = OpenAICompatibleClient(api_key='test-key')
    client._client = sdk
    client._token_param_cache['test-model'] = 'max_tokens'
    usage_callback = MagicMock()
    client.set_usage_callback(usage_callback)
    monkeypatch.setattr('llm_client.get_effective_provider',
                        lambda: 'openai-compatible')

    ctx = run_context.begin('t', episode_id, run_id='run-1')
    try:
        response, error = llm_call.call_llm_for_window(
            llm_client=client,
            model='test-model',
            system_prompt='sys',
            prompt='user',
            llm_timeout=1.0,
            max_retries=1,
            max_tokens=8192,
            slug='t',
            episode_id=episode_id,
            window_label='w',
            reasoning_effort='high',
            pass_name=PASS_AD_DETECTION_1,
        )
        notices = ctx.thinking_notices('run-1')
    finally:
        run_context.end(ctx)

    assert error is None
    assert response.content == '[]'
    first, rejected, recovered = sdk.chat.completions.create.call_args_list
    assert first.kwargs['reasoning_effort'] == 'high'
    assert rejected.kwargs['reasoning_effort'] == 'none'
    assert 'reasoning_effort' not in recovered.kwargs
    assert recovered.kwargs['max_tokens'] == 4096
    assert is_fallback_set(episode_id, PASS_AD_DETECTION_1) is True
    assert notices == [{
        'pass': PASS_AD_DETECTION_1,
        'provider': 'openai-compatible',
        'model': 'test-model',
        'requested': 'none',
        'compatibility': 'required',
        'fallback': {
            'max_tokens': 4096,
            'temperature': 0.0,
            'reasoning_effort': None,
        },
    }]
    assert 'private-provider-detail' not in str(notices)
    assert 'private-provider-detail' not in caplog.text
    assert [item.args for item in usage_callback.call_args_list] == [
        ('test-model', {'input_tokens': 100, 'output_tokens': 8192}),
        ('test-model', {'input_tokens': 100, 'output_tokens': 2}),
    ]
    assert len(no_retry_wait) == 1


@pytest.mark.parametrize('message, expected', [
    ('reasoning is required', 'required'),
    ('thinking must be enabled for this model', 'required'),
    ('this model does not support reasoning_effort', 'unsupported'),
    ('thinking is unavailable', 'unsupported'),
    ('invalid budget_tokens value', 'incompatible'),
    ('max_tokens is unsupported', None),
])
def test_reasoning_rejection_classification_is_conservative(message, expected):
    assert classify_reasoning_rejection(
        FakeProviderError(message, status_code=400)) == expected


def test_secondary_reasoning_exhaustion_disables_final_retry(no_retry_wait):
    transient = FakeProviderError('503 unavailable', status_code=503)
    exhausted = SimpleNamespace(
        content='', reasoning_present=True, finish_reason='length')
    answered = SimpleNamespace(content='[]')
    client = _SequenceClient(transient, exhausted, answered)

    response, error = llm_call.call_llm_for_window(
        llm_client=client,
        model='test-model',
        system_prompt='sys',
        prompt='user',
        llm_timeout=1.0,
        max_retries=0,
        max_tokens=4096,
        slug='t',
        episode_id='e',
        window_label='w',
        reasoning_effort='high',
    )

    assert error is None
    assert response is answered
    assert [call['reasoning_effort'] for call in client.call_kwargs] == [
        'high', 'high', 'none',
    ]
    assert no_retry_wait == [2, 5]


def test_empty_choices_with_full_reasoning_usage_uses_reasoning_fallback(
        monkeypatch):
    provider_response = SimpleNamespace(
        choices=[],
        usage=SimpleNamespace(
            prompt_tokens=10,
            completion_tokens=100,
            completion_tokens_details=SimpleNamespace(reasoning_tokens=100),
        ),
    )
    sdk = MagicMock()
    sdk.chat.completions.create.return_value = provider_response
    client = OpenAICompatibleClient(api_key='test-key')
    client._client = sdk
    client._token_param_cache['test-model'] = 'max_completion_tokens'
    monkeypatch.setattr('llm_client.get_effective_provider',
                        lambda: 'openai-compatible')
    kwargs = {
        'model': 'test-model',
        'max_tokens': 100,
        'system': 'system',
        'messages': [{'role': 'user', 'content': 'prompt'}],
        'timeout': 1.0,
        'response_format': None,
        'reasoning_effort': 'high',
        'episode_id': None,
        'pass_name': None,
    }

    with pytest.raises(llm_call.ReasoningExhaustedError):
        llm_call._call_once(client, kwargs, 'test-model')


def test_reasoning_fallback_failure_stays_a_failed_window(no_retry_wait):
    exhausted = SimpleNamespace(
        content='', reasoning_present=True, finish_reason='max_tokens')
    client = _SequenceClient(exhausted)

    response, error = call_window(client, max_retries=3)

    assert response is None
    assert isinstance(error, llm_call.ReasoningExhaustedError)
    assert client.calls == 6
    assert client.call_kwargs[0]['reasoning_effort'] is None
    assert all(
        call['reasoning_effort'] == 'none' for call in client.call_kwargs[1:]
    )


def test_nonempty_completion_does_not_change_request_or_retry(no_retry_wait):
    result = SimpleNamespace(
        content='[]', reasoning_present=True, finish_reason='length')
    client = _SequenceClient(result)

    response, error = call_window(client, max_retries=3)

    assert response is result
    assert error is None
    assert client.calls == 1
    assert client.call_kwargs[0]['reasoning_effort'] is None
    assert no_retry_wait == []


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
        usage=SimpleNamespace(
            prompt_tokens=10, completion_tokens=4,
            completion_tokens_details=None,
        ),
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
