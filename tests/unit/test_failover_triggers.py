"""Which errors move a target to its failover config (#806)."""
import httpx
import openai
import pytest
import anthropic
import httpx2

from tests.app_bootstrap import bootstrap
bootstrap('failover_triggers_test_')

from config import ModelNotConfiguredError  # noqa: E402
from utils.circuit_breaker import CircuitBreakerOpen  # noqa: E402
from llm_client import (  # noqa: E402
    ProviderRequestRejectedError, is_connectivity_error, is_failover_trigger_error,
    is_retryable_error,
)


def _api_status(status):
    resp = httpx.Response(status, request=httpx.Request('POST', 'http://example.com'))
    return openai.APIStatusError('x', response=resp, body=None)


def _anthropic_api_status(status):
    request = httpx2.Request('POST', 'http://example.com')
    response = httpx2.Response(status, request=request)
    return anthropic.APIStatusError('x', response=response, body=None)


@pytest.mark.parametrize('status', [401, 402, 403, 404, 500, 502, 503, 529])
def test_status_triggers(status):
    assert is_failover_trigger_error(_api_status(status)) is True


@pytest.mark.parametrize('status', [400, 422, 429])
def test_status_does_not_trigger(status):
    error = _api_status(status)
    assert is_failover_trigger_error(error) is False
    if status != 429:
        assert is_retryable_error(error) is False
        assert is_connectivity_error(error) is False


@pytest.mark.parametrize('error', [_api_status(408), _anthropic_api_status(408)])
def test_http_request_timeout_retries_and_triggers_failover(error):
    assert is_retryable_error(error) is True
    assert is_connectivity_error(error) is True
    assert is_failover_trigger_error(error) is True


def test_connection_and_timeout_trigger():
    req = httpx.Request('POST', 'http://example.com')
    assert is_failover_trigger_error(openai.APIConnectionError(request=req)) is True
    assert is_failover_trigger_error(openai.APITimeoutError(request=req)) is True


def test_breaker_open_triggers():
    assert is_failover_trigger_error(CircuitBreakerOpen('llm', 30)) is True


def test_non_provider_errors_do_not_trigger():
    assert is_failover_trigger_error(ModelNotConfiguredError('claude_model')) is False
    assert is_failover_trigger_error(ValueError('bad json')) is False


@pytest.mark.parametrize('status_code', [402, 404])
def test_provider_request_rejected_does_not_trigger(status_code):
    assert is_failover_trigger_error(
        ProviderRequestRejectedError('x', status_code=status_code)) is False
