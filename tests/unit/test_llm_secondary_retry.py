"""Provider error classification after the primary LLM retry loop."""
import os
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from tests.app_bootstrap import bootstrap

bootstrap('llm_secondary_retry_test_')

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
from utils.circuit_breaker import CircuitBreakerOpen


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

    def _record(delay):
        sleeps.append(delay)
        return True

    monkeypatch.setattr(llm_call, '_sleep_before_retry', _record)
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
def test_reasoning_exhaustion_retry_disables_reasoning(
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
        phase_key='test',
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
    # Re-asking with reasoning off is a different request, not a transient
    # failure, so it does not wait out a backoff first.
    assert no_retry_wait == []


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
        phase_key='test',
        reasoning_effort=2048,
    )

    assert error is None
    assert response.content == '[]'
    first, second = sdk.messages.create.call_args_list
    assert first.kwargs['thinking'] == {
        'type': 'enabled', 'budget_tokens': 2048,
    }
    assert 'thinking' not in second.kwargs
    assert no_retry_wait == []


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
            phase_key='test',
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
    assert no_retry_wait == []


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
        phase_key='test',
        reasoning_effort='high',
    )

    assert error is None
    assert response is answered
    assert [call['reasoning_effort'] for call in client.call_kwargs] == [
        'high', 'high', 'none',
    ]
    # The reasoning retry rides inside per-window retry 1 rather than spending
    # retry 2, so the window answers a backoff sooner.
    assert no_retry_wait == [2]


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
    """Two calls, not the whole retry ladder: with reasoning already off, another
    attempt buys the same truncated answer."""
    exhausted = SimpleNamespace(
        content='', reasoning_present=True, finish_reason='max_tokens')
    client = _SequenceClient(exhausted)

    response, error = call_window(client, max_retries=3)

    assert response is None
    assert isinstance(error, llm_call.ReasoningExhaustedError)
    assert client.calls == 2
    assert client.call_kwargs[0]['reasoning_effort'] is None
    assert client.call_kwargs[1]['reasoning_effort'] == 'none'


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


def test_secondary_retry_waits_for_provider_retry_after(monkeypatch, no_retry_wait):
    """A 429 under the hold threshold must wait its reset, not the 2s/5s floor."""
    monkeypatch.setattr(llm_call, 'is_rate_limit_hold_enabled', lambda: True)
    rate_limit = FakeProviderError(
        '429 rate limit', status_code=429,
        response=FakeResponse(headers={'Retry-After': '60'}),
    )
    client = _SequenceClient(rate_limit)

    response, error = _secondary_result(client)

    assert response is None
    assert error is rate_limit
    assert client.calls == 3
    assert len(no_retry_wait) == 2
    assert 60.0 <= no_retry_wait[0] <= 62.0
    assert no_retry_wait[1] == 5


def test_secondary_retry_honors_a_reset_under_the_hold_threshold(
        monkeypatch, no_retry_wait):
    """A reset below MIN_HOLD_RESET_SECONDS never becomes a queue hold, so the
    worker waits it out once rather than cutting it short and skipping review."""
    monkeypatch.setattr(llm_call, 'is_rate_limit_hold_enabled', lambda: False)
    rate_limit = FakeProviderError(
        '429 rate limit', status_code=429,
        response=FakeResponse(headers={'Retry-After': '240'}),
    )
    client = _SequenceClient(rate_limit)

    response, error = _secondary_result(client)

    assert response is None
    assert error is rate_limit
    assert len(no_retry_wait) == 2
    assert 240.0 <= no_retry_wait[0] <= 242.0
    assert no_retry_wait[1] == 5


def test_secondary_retry_stops_waiting_on_shutdown(monkeypatch):
    """A container stop must not sit out a long provider reset."""
    slept = []
    monkeypatch.setattr(llm_call.time, 'sleep', slept.append)
    monkeypatch.setattr(llm_call, '_shutdown_requested', lambda: True)
    monkeypatch.setattr(llm_call, 'is_rate_limit_hold_enabled', lambda: False)
    rate_limit = FakeProviderError(
        '429 rate limit', status_code=429,
        response=FakeResponse(headers={'Retry-After': '240'}),
    )
    client = _SequenceClient(rate_limit)

    response, error = _secondary_result(client)

    assert response is None
    assert error is rate_limit
    assert slept == []
    assert client.calls == 1


def test_the_retry_wait_never_imports_the_app():
    """main_app constructs singletons, takes the runtime lock, and starts the
    background threads at import time, so the wait must not pull it in."""
    script = (
        "import sys; sys.path.insert(0, 'src');"
        "from utils import llm_call;"
        "llm_call._sleep_before_retry(0);"
        "loaded = [m for m in sys.modules if m == 'main_app' or m.startswith('main_app.')];"
        "assert not loaded, loaded"
    )
    repo_root = os.path.join(os.path.dirname(__file__), '..', '..')
    result = subprocess.run([sys.executable, '-c', script], cwd=repo_root,
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_retry_wait_is_sliced_so_a_shutdown_lands_quickly(monkeypatch):
    """The wait polls the shutdown signal instead of sleeping straight through."""
    slept = []
    monkeypatch.setattr(llm_call.time, 'sleep', slept.append)
    monkeypatch.setattr(llm_call, '_shutdown_requested', lambda: False)

    assert llm_call._sleep_before_retry(240) is True
    assert sum(slept) == 240
    assert max(slept) <= llm_call.RETRY_SLEEP_SLICE_SECONDS

    checks = {'n': 0}

    def _requested():
        checks['n'] += 1
        return checks['n'] > 2

    slept.clear()
    monkeypatch.setattr(llm_call, '_shutdown_requested', _requested)
    assert llm_call._sleep_before_retry(240) is False
    assert sum(slept) < 240


def test_secondary_retry_without_retry_after_keeps_fixed_backoff(
        monkeypatch, no_retry_wait):
    """No provider-reported reset: the 2s/5s per-window backoff still applies."""
    monkeypatch.setattr(llm_call, 'is_rate_limit_hold_enabled', lambda: True)
    rate_limit = FakeProviderError('429 rate limit hit', status_code=429)
    client = _SequenceClient(rate_limit)

    response, error = _secondary_result(client)

    assert response is None
    assert error is rate_limit
    assert client.calls == 3
    assert no_retry_wait == [2, 5]


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


def test_breaker_retry_delay_adds_margin_past_the_cooldown():
    """Jitter alone (0-0.25s) is why a retry landed 1s inside a 60s cooldown."""
    error = CircuitBreakerOpen('llm-api:test', seconds_until_retry=59.0)

    delay = llm_call._breaker_retry_delay(None, error, base_delay=5.0)

    floor = 59.0 + llm_call.BREAKER_RETRY_MARGIN_SECONDS
    assert floor <= delay <= floor + 0.25


def test_final_fallback_attempt_gets_one_more_after_breaker_cooldown(
        no_retry_wait, caplog):
    breaker_open = CircuitBreakerOpen('llm-api:test', seconds_until_retry=5.0)
    answered = SimpleNamespace(content='[]')
    client = _SequenceClient(breaker_open, breaker_open, breaker_open, answered)

    with caplog.at_level('WARNING'):
        response, error = call_window(client, max_retries=0)

    assert error is None
    assert response is answered
    assert client.calls == 4
    assert 'per-window retry 3/3 after breaker cooldown' in caplog.text


def test_final_fallback_attempt_skipped_past_the_wait_cap(no_retry_wait, caplog):
    """A cooldown longer than the 90s cap gives up as before, no extra attempt."""
    breaker_open = CircuitBreakerOpen('llm-api:test', seconds_until_retry=95.0)
    client = _SequenceClient(breaker_open, breaker_open, breaker_open)

    with caplog.at_level('WARNING'):
        response, error = call_window(client, max_retries=0)

    assert response is None
    assert error is breaker_open
    assert client.calls == 3
    assert 'per-window retry 3/3' not in caplog.text


def test_final_fallback_attempt_only_follows_circuit_breaker_open(
        no_retry_wait, caplog):
    """A non-breaker error never earns the extra attempt."""
    transient = FakeProviderError('503 unavailable', status_code=503)
    client = _SequenceClient(transient, transient, transient)

    with caplog.at_level('WARNING'):
        response, error = call_window(client, max_retries=0)

    assert response is None
    assert error is transient
    assert client.calls == 3
    assert 'per-window retry 3/3' not in caplog.text


def test_empty_completion_carries_usage_for_the_ledger(monkeypatch):
    """The provider still billed the empty call, so the raised error must
    carry its usage for the ledger to finalize the attempt with."""
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
    with pytest.raises(llm_call.EmptyCompletionError) as excinfo:
        llm_call._call_once(client, kwargs, 'test-model')

    assert excinfo.value.response.usage == {'input_tokens': 10, 'output_tokens': 4}
