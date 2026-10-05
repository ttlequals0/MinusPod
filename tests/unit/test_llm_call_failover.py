"""call_llm re-dispatches once on the failover route after a trigger error (#806)."""
from unittest.mock import MagicMock, patch

import pytest
import httpx
import openai

from tests.app_bootstrap import bootstrap
bootstrap('llm_call_failover_test_')

import failover
import llm_route
from cancel import ProcessingCancelled
from llm_client import (ProviderAccountChangedError, ProviderRateLimitedError,
                        StructuralRateLimitError, ProviderRequestRejectedError, is_failover_trigger_error)
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


def _bad_request():
    import httpx, openai
    resp = httpx.Response(400, request=httpx.Request('POST', 'http://example.com'))
    return openai.BadRequestError('bad request', response=resp, body=None)


def _not_found():
    import httpx, openai
    resp = httpx.Response(404, request=httpx.Request('POST', 'http://example.com'))
    return openai.NotFoundError('not found', response=resp, body=None)


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


@pytest.mark.parametrize('held', [False, True])
def test_provider_rate_limit_uses_independent_standby(no_sleep, held):
    resp = httpx.Response(429, headers={'retry-after': '300'},
                          request=httpx.Request('POST', 'http://example.com'))
    client = MagicMock(); client.create_message.side_effect = openai.RateLimitError('x', response=resp, body=None)
    standby = MagicMock(); standby.create_message.return_value = {'content': 'ok'}
    with patch.object(llm_call, 'is_rate_limit_hold_enabled', return_value=held), \
            patch.object(failover, 'is_configured', return_value=True), \
            patch.object(failover, 'trigger', return_value=True) as trig, \
            patch.object(llm_call, '_failover_route', return_value=FAILOVER_ROUTE), \
            patch.object(llm_call, 'client_for_route', return_value=standby), \
            patch.object(llm_call, '_ledger_call_once', side_effect=lambda c, kw, m, **k: c.create_message(**kw)):
        response, error = _call(client)
    assert response == {'content': 'ok'} and error is None
    trig.assert_called_once()
    standby.create_message.assert_called_once()


@pytest.mark.parametrize('error', [
    ProviderRateLimitedError('manual cap', 60, manual=True),
    StructuralRateLimitError('requested tokens exceed cap'),
    ProviderRequestRejectedError('bad request', 400),
    ProviderRequestRejectedError('bad parameters', 422),
])
def test_manual_and_invalid_request_limits_cannot_bypass_caps(error, no_sleep):
    assert not is_failover_trigger_error(error)
    client = MagicMock(); client.create_message.side_effect = error
    with patch.object(llm_call, '_manual_rate_limit_error', return_value=None), \
            patch.object(failover, 'is_configured', return_value=True), \
            patch.object(failover, 'trigger') as trigger, \
            patch.object(llm_call, '_ledger_call_once', side_effect=lambda c, kw, m, **k: c.create_message(**kw)):
        response, last_error = _call(client)
    assert response is None and last_error is error
    trigger.assert_not_called()


def test_no_redispatch_when_unconfigured(no_sleep):
    client = MagicMock(); client.create_message.side_effect = _outage()
    with patch.object(failover, 'is_configured', return_value=False), \
            patch.object(failover, 'trigger') as trig, \
            patch.object(llm_call, '_ledger_call_once', side_effect=lambda c, kw, m, **k: c.create_message(**kw)):
        response, err = _call(client)
    assert response is None
    trig.assert_not_called()


def test_standby_rejection_replaces_primary_connectivity_error(no_sleep):
    primary_error = _outage()
    primary = MagicMock(); primary.create_message.side_effect = primary_error
    standby_error = _bad_request()
    fo_client = MagicMock(); fo_client.create_message.side_effect = standby_error
    with patch.object(failover, 'is_configured', return_value=True), \
            patch.object(failover, 'trigger', return_value=True), \
            patch.object(llm_call, '_failover_route', return_value=FAILOVER_ROUTE), \
            patch.object(llm_call, 'client_for_route', return_value=fo_client), \
            patch.object(llm_call, '_ledger_call_once', side_effect=lambda c, kw, m, **k: c.create_message(**kw)):
        response, err = _call(primary)
    assert response is None
    assert err is standby_error
    assert err.__context__ is primary_error
    assert fo_client.create_message.call_count >= 1


def test_standby_outage_replaces_primary_not_found_error(no_sleep):
    primary_error = _not_found()
    primary = MagicMock(); primary.create_message.side_effect = primary_error
    standby_error = _outage()
    fo_client = MagicMock(); fo_client.create_message.side_effect = standby_error
    with patch.object(failover, 'is_configured', return_value=True), \
            patch.object(failover, 'trigger', return_value=True), \
            patch.object(llm_call, '_failover_route', return_value=FAILOVER_ROUTE), \
            patch.object(llm_call, 'client_for_route', return_value=fo_client), \
            patch.object(llm_call, '_ledger_call_once', side_effect=lambda c, kw, m, **k: c.create_message(**kw)):
        response, err = _call(primary)
    assert response is None
    assert err is standby_error
    assert err.__context__ is primary_error
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


def test_trigger_raising_returns_original_error(no_sleep):
    primary_error = _outage()
    primary = MagicMock(); primary.create_message.side_effect = primary_error
    with patch.object(failover, 'is_configured', return_value=True), \
            patch.object(failover, 'trigger', side_effect=RuntimeError('db locked')), \
            patch.object(llm_call, '_ledger_call_once', side_effect=lambda c, kw, m, **k: c.create_message(**kw)):
        response, err = _call(primary)
    assert response is None and err is primary_error


@pytest.mark.parametrize('standby_error', [
    ProviderRateLimitedError('standby hold', 60, provider_key='openai-compatible', credential_slot='failover'),
    openai.AuthenticationError('rejected', response=httpx.Response(
        401, request=httpx.Request('POST', 'http://example.com')), body=None),
    ProcessingCancelled('cancelled'),
    ProviderAccountChangedError('account changed', credential_slot='failover'),
])
def test_standby_terminal_error_retains_type_and_primary_context(no_sleep, standby_error):
    primary_error = _not_found()
    primary = MagicMock(); primary.create_message.side_effect = primary_error
    standby = MagicMock(); standby.create_message.side_effect = standby_error
    setup_error = standby_error if isinstance(standby_error, (ProcessingCancelled, ProviderAccountChangedError)) else None
    with patch.object(failover, 'is_configured', return_value=True), \
            patch.object(failover, 'trigger', return_value=True), \
            patch.object(llm_call, '_failover_route', return_value=FAILOVER_ROUTE), \
            patch.object(llm_call, 'client_for_route', return_value=standby, side_effect=setup_error), \
            patch.object(llm_call, '_fire_auth_failure_webhook'), \
            patch.object(llm_call, '_ledger_call_once', side_effect=lambda c, kw, m, **k: c.create_message(**kw)):
        response, error = _call(primary)
    assert response is None
    assert error is standby_error
    assert error.__context__ is primary_error


def test_missing_standby_route_returns_original_error(no_sleep):
    primary_error = _not_found()
    primary = MagicMock(); primary.create_message.side_effect = primary_error
    with patch.object(failover, 'is_configured', return_value=True), \
            patch.object(failover, 'trigger', return_value=False), \
            patch.object(llm_call, '_failover_route', return_value=None), \
            patch.object(llm_call, '_ledger_call_once', side_effect=lambda c, kw, m, **k: c.create_message(**kw)):
        response, error = _call(primary)
    assert response is None and error is primary_error


def test_failover_downgrades_json_schema_when_model_lacks_support(no_sleep):
    primary = MagicMock(); primary.create_message.side_effect = _outage()
    fo_client = MagicMock(); fo_client.create_message.return_value = {'content': 'ok'}
    schema = llm_call.json_schema_format('x', {'type': 'object'})
    with patch.object(failover, 'is_configured', return_value=True), \
            patch.object(failover, 'trigger', return_value=True), \
            patch.object(llm_call, '_failover_route', return_value=FAILOVER_ROUTE), \
            patch.object(llm_call, 'client_for_route', return_value=fo_client), \
            patch.object(llm_call, 'supports_json_schema_for_calls', return_value=False), \
            patch.object(llm_call, '_ledger_call_once', side_effect=lambda c, kw, m, **k: c.create_message(**kw)):
        _call(primary, response_format=schema)
    assert primary.create_message.call_args.kwargs['response_format'] == schema
    assert fo_client.create_message.call_args.kwargs['response_format'] == {'type': 'json_object'}


def test_detector_switches_model_and_slot_mid_pass():
    """Window 2 after a window-1 trigger goes out with the failover model and slot."""
    import ad_detector
    import run_context
    from ad_detector import AdDetector, PASS_AD_DETECTION_1

    cfg = {'provider': 'openai-compatible', 'base_url': 'http://127.0.0.1:11434/v1',
           'timeout': None, 'max_retries': None,
           'models': {'detection': 'qwen3:8b', 'review': '', 'verification': '', 'chapters': ''}}
    active = {'on': False}
    sent = []

    def fake_window_call(**kw):
        sent.append(kw)
        active['on'] = True
        return None, _outage()

    detector = AdDetector.__new__(AdDetector)
    detector._llm_client_override = MagicMock()
    ctx = run_context.begin('example-podcast', 'a1b2c3d4e5f6', run_id='r-fo1')
    try:
        ctx.set_route_snapshot({'detection': {
            'provider_key': 'anthropic', 'configured_model': 'claude-sonnet-5',
            'base_url': None, 'credential_slot': 'primary'}})
        with patch.object(failover, 'is_active', side_effect=lambda t: active['on'] and t == 'llm:primary'), \
                patch.object(failover, 'is_configured', return_value=True), \
                patch.object(failover, 'failover_llm_config', return_value=cfg), \
                patch.object(ad_detector, 'call_llm_for_window', side_effect=fake_window_call):
            for label in ('Window 1', 'Window 2'):
                detector._call_llm_for_window(
                    model='claude-sonnet-5', system_prompt='s', prompt='p', llm_timeout=1,
                    max_retries=0, slug='example-podcast', episode_id='a1b2c3d4e5f6',
                    window_label=label, pass_name=PASS_AD_DETECTION_1)
    finally:
        run_context.end(ctx)
    assert (sent[0]['model'], sent[0]['credential_slot']) == ('claude-sonnet-5', 'primary')
    assert (sent[1]['model'], sent[1]['credential_slot'], sent[1]['provider']) == (
        'qwen3:8b', 'failover', 'openai-compatible')


def test_exhausted_provider_daily_quota_uses_standby(no_sleep):
    body = {'error': {'code': 429, 'status': 'RESOURCE_EXHAUSTED', 'details': [{
        '@type': 'type.googleapis.com/google.rpc.QuotaFailure',
        'violations': [{'quotaId': 'GenerateRequestsPerDayPerProjectPerModel-FreeTier',
                        'quotaValue': '50', 'quotaDimensions': {'model': 'example-model'}}]}]}}
    response = httpx.Response(429, request=httpx.Request('POST', 'http://example.com'))
    primary_error = openai.RateLimitError('daily quota', response=response, body=body)
    primary = MagicMock(); primary.create_message.side_effect = primary_error
    standby = MagicMock(); standby.create_message.return_value = {'content': 'ok'}
    with patch.object(failover, 'is_configured', return_value=True), \
            patch.object(failover, 'trigger', return_value=True) as trigger, \
            patch.object(llm_call, '_failover_route', return_value=FAILOVER_ROUTE), \
            patch.object(llm_call, 'client_for_route', return_value=standby), \
            patch.object(llm_call, '_ledger_call_once', side_effect=lambda c, kw, m, **k: c.create_message(**kw)):
        result, error = _call(primary)
    assert result == {'content': 'ok'} and error is None
    primary.create_message.assert_called_once()
    standby.create_message.assert_called_once()
    trigger.assert_called_once()


def test_normalized_provider429_can_use_standby():
    assert is_failover_trigger_error(ProviderRateLimitedError('provider reset', 300))
