"""Which errors move a target to its failover config (#806)."""
import httpx
import openai
import pytest

from tests.app_bootstrap import bootstrap
bootstrap('failover_triggers_test_')

from llm_client import is_failover_trigger_error, ProviderRequestRejectedError  # noqa: E402


def _api_status(status):
    resp = httpx.Response(status, request=httpx.Request('POST', 'http://example.com'))
    return openai.APIStatusError('x', response=resp, body=None)


@pytest.mark.parametrize('status', [401, 402, 403, 404, 500, 502, 503, 529])
def test_status_triggers(status):
    assert is_failover_trigger_error(_api_status(status)) is True


@pytest.mark.parametrize('status', [400, 422, 429])
def test_status_does_not_trigger(status):
    assert is_failover_trigger_error(_api_status(status)) is False


def test_connection_and_timeout_trigger():
    req = httpx.Request('POST', 'http://example.com')
    assert is_failover_trigger_error(openai.APIConnectionError(request=req)) is True
    assert is_failover_trigger_error(openai.APITimeoutError(request=req)) is True


def test_breaker_open_triggers():
    from utils.circuit_breaker import CircuitBreakerOpen
    assert is_failover_trigger_error(CircuitBreakerOpen('llm', 30)) is True


def test_non_provider_errors_do_not_trigger():
    from config import ModelNotConfiguredError
    assert is_failover_trigger_error(ModelNotConfiguredError('claude_model')) is False
    assert is_failover_trigger_error(ValueError('bad json')) is False


@pytest.mark.parametrize('status_code', [402, 404])
def test_provider_request_rejected_does_not_trigger(status_code):
    assert is_failover_trigger_error(
        ProviderRequestRejectedError('x', status_code=status_code)) is False
