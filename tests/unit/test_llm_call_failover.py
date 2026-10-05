"""call_llm re-dispatches once on the failover route after a trigger error (#806)."""
from unittest.mock import MagicMock, patch

import pytest

from tests.app_bootstrap import bootstrap
bootstrap('llm_call_failover_test_')

import failover
import llm_route
from utils import llm_call

FAILOVER_ROUTE = llm_route.Route(
    phase='detection', provider_key='openai-compatible', model_id='qwen3:8b',
    base_url='http://127.0.0.1:11434/v1', slot='failover', credential_slot='failover',
    account_id=llm_route.account_identity('openai-compatible', 'http://127.0.0.1:11434/v1'))


def _call(client, **overrides):
    kwargs = dict(llm_client=client, model='claude-sonnet-5', system_prompt='s', prompt='p',
                  llm_timeout=1.0, max_retries=0, max_tokens=10, slug='example-podcast',
                  episode_id='a1b2c3d4e5f6', call_label='t', phase_key='detection',
                  provider='anthropic', credential_slot='primary')
    kwargs.update(overrides)
    return llm_call.call_llm(**kwargs)


def _outage():
    import httpx, openai
    return openai.APIConnectionError(request=httpx.Request('POST', 'http://example.com'))


@pytest.fixture
def no_sleep():
    with patch.object(llm_call, '_sleep_before_retry', return_value=True):
        yield


def test_trigger_error_redispatches_on_failover(no_sleep):
    primary = MagicMock(); primary.create_message.side_effect = _outage()
    fo_client = MagicMock(); fo_client.create_message.return_value = {'content': 'ok'}
    with patch.object(failover, 'is_configured', return_value=True), \
            patch.object(failover, 'trigger', return_value=True) as trig, \
            patch.object(llm_call, '_failover_route', return_value=FAILOVER_ROUTE), \
            patch.object(llm_call, 'client_for_route', return_value=fo_client), \
            patch.object(llm_call, '_ledger_call_once', side_effect=lambda c, kw, m, **k: c.create_message(**kw)):
        response, err = _call(primary)
    assert err is None and response == {'content': 'ok'}
    trig.assert_called_once()
    assert trig.call_args.args[0] == 'llm:primary'
    assert fo_client.create_message.call_args.kwargs['model'] == 'qwen3:8b'


def test_no_redispatch_when_already_on_failover(no_sleep):
    client = MagicMock(); client.create_message.side_effect = _outage()
    with patch.object(failover, 'is_configured', return_value=True), \
            patch.object(failover, 'trigger') as trig, \
            patch.object(llm_call, '_ledger_call_once', side_effect=lambda c, kw, m, **k: c.create_message(**kw)):
        response, err = _call(client, credential_slot='failover', provider='openai-compatible')
    assert response is None and err is not None
    trig.assert_not_called()


def test_no_redispatch_on_rate_limit(no_sleep):
    import httpx, openai
    resp = httpx.Response(429, request=httpx.Request('POST', 'http://example.com'))
    client = MagicMock(); client.create_message.side_effect = openai.RateLimitError('x', response=resp, body=None)
    with patch.object(failover, 'is_configured', return_value=True), \
            patch.object(failover, 'trigger') as trig, \
            patch.object(llm_call, '_ledger_call_once', side_effect=lambda c, kw, m, **k: c.create_message(**kw)):
        _call(client)
    trig.assert_not_called()


def test_no_redispatch_when_unconfigured(no_sleep):
    client = MagicMock(); client.create_message.side_effect = _outage()
    with patch.object(failover, 'is_configured', return_value=False), \
            patch.object(failover, 'trigger') as trig, \
            patch.object(llm_call, '_ledger_call_once', side_effect=lambda c, kw, m, **k: c.create_message(**kw)):
        response, err = _call(client)
    assert response is None
    trig.assert_not_called()


def test_failover_failure_returns_original_style_error(no_sleep):
    primary = MagicMock(); primary.create_message.side_effect = _outage()
    fo_client = MagicMock(); fo_client.create_message.side_effect = _outage()
    with patch.object(failover, 'is_configured', return_value=True), \
            patch.object(failover, 'trigger', return_value=True), \
            patch.object(llm_call, '_failover_route', return_value=FAILOVER_ROUTE), \
            patch.object(llm_call, 'client_for_route', return_value=fo_client), \
            patch.object(llm_call, '_ledger_call_once', side_effect=lambda c, kw, m, **k: c.create_message(**kw)):
        response, err = _call(primary)
    assert response is None and err is not None
    assert fo_client.create_message.call_count >= 1


def test_failover_ledger_rows_use_failover_slot(no_sleep):
    """The failover dispatch's ledger call carries credential_slot='failover'
    and provider_key=route.provider_key (#806)."""
    primary = MagicMock(); primary.create_message.side_effect = _outage()
    fo_client = MagicMock(); fo_client.create_message.return_value = {'content': 'ok'}
    seen_calls = []

    def _fake_ledger(c, kw, m, **k):
        seen_calls.append(k)
        return c.create_message(**kw)

    with patch.object(failover, 'is_configured', return_value=True), \
            patch.object(failover, 'trigger', return_value=True), \
            patch.object(llm_call, '_failover_route', return_value=FAILOVER_ROUTE), \
            patch.object(llm_call, 'client_for_route', return_value=fo_client), \
            patch.object(llm_call, '_ledger_call_once', side_effect=_fake_ledger):
        _call(primary)
    failover_calls = [k for k in seen_calls if k['credential_slot'] == 'failover']
    assert len(failover_calls) >= 1
    assert all(k['provider_key'] == 'openai-compatible' for k in failover_calls)
