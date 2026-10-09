"""System One request accounting stays at the HTTP attempt boundary."""
import time
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import httpx
import pytest
from openai import APIStatusError, RateLimitError
from tests.app_bootstrap import bootstrap

bootstrap('systemone_dispatch_test_')

import run_context
from cancel import ProcessingCancelled, ProcessingOwnershipLost
from database import Database
from llm_client import ProviderRateLimitedError, is_review_inconclusive_error, is_retryable_error
from rate_limit_hold import reserve_provider_request
from systemone.transport import (
    SystemOneTransport, DispatchResponse, _ledger_response, _request_error, _reserved_tokens,
    dispatch_systemone_request,
)
from systemone.protocol import JevReviewValidationError
from utils.llm_call import call_llm
from utils.llm_call import _reservation_refused, _terminal_error, _sleep_before_retry, _fallback_delay
from utils.retry import calculate_backoff


class SequenceClient:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = 0
        self.kwargs = []

    def post(self, *args, **kwargs):
        self.calls += 1
        self.kwargs.append(kwargs)
        return next(self.responses)


def _response(status, body):
    return httpx.Response(status, json=body,
                          request=httpx.Request('POST', 'https://example.test'))


def _metadata():
    return {
        'run_id': None, 'podcast_id': 1, 'episode_id': 'ep1',
        'phase_key': 'detection', 'invoking_pass': 1,
        'provider_key': 'systemone-compatible', 'credential_slot': 'primary',
        'configured_model': 'test-model', 'slug': None,
        'call_label': 'window-1', 'max_retries': 1,
        'database': Database(), 'reserve_request': reserve_provider_request,
        'reservation_refused': _reservation_refused,
        'cancel_check': lambda: None,
        'cancel_exceptions': (ProcessingCancelled, ProcessingOwnershipLost),
        'retryable': is_retryable_error,
        'inconclusive': is_review_inconclusive_error,
        'retry_delay': lambda error, attempt: _fallback_delay(
            error, calculate_backoff(attempt), attempt == 0),
        'sleep_retry': _sleep_before_retry,
        'terminal_error': _terminal_error,
    }


def test_missing_usage_is_unknown_not_a_transport_crash():
    response = _ledger_response({'answers': {}})
    assert response.usage == {}
    assert response.provider_reported_cost_usd is None


def test_reserved_tokens_count_each_serialized_chunk_body_and_ignore_chat_budget(temp_db):
    payloads = [
        {'model': 'test-model', 'state': {'guidance': 'x' * 2048},
         'questions': {f'q{index}': {'type': 'noul'}}}
        for index in range(2)
    ]
    client = SequenceClient([_response(200, {}), _response(200, {})])
    metadata = _metadata()
    metadata['max_tokens'] = 1_000_000
    run_context.begin_llm_dispatch_context(metadata)
    try:
        for payload in payloads:
            dispatch_systemone_request(
                payload, url='https://example.test', api_key=None,
                model='test-model', timeout=2, request_kind='detection',
                http_client=client,
            )
    finally:
        run_context.end_llm_dispatch_context()

    rows = temp_db.get_connection().execute(
        'SELECT reserved_tokens FROM llm_call_usage ORDER BY rowid').fetchall()
    assert [row['reserved_tokens'] for row in rows] == [
        _reserved_tokens(payload) for payload in payloads]
    assert rows[0]['reserved_tokens'] > 1_000


def test_status_errors_use_sdk_types_and_keep_retry_after_headers():
    response = _response(429, {'error': 'busy'})
    response.headers['Retry-After'] = '2'
    error = _request_error(429, response, {'error': 'busy'})
    assert isinstance(error, RateLimitError)
    assert error.response.headers['Retry-After'] == '2'
    assert isinstance(_request_error(422, _response(422, {}), {}), APIStatusError)


def test_probe_validation_keeps_source_error_and_billed_usage():
    response = _response(200, {
        'usage': {'input_tokens': 4, 'output_tokens': 2}, 'answers': {},
    })
    transport = SystemOneTransport(
        url='https://example.test', api_key=None, model='test-model', timeout=2,
        http_client=SequenceClient([response]),
    )

    def reject(_body):
        raise JevReviewValidationError('answers_keys')

    with pytest.raises(JevReviewValidationError) as raised:
        transport.probe_once({}, validate=reject)
    assert raised.value.rule == 'answers_keys'
    assert raised.value.usage_response.usage == {
        'input_tokens': 4, 'output_tokens': 2,
    }


def test_probe_invalid_json_keeps_headers_and_is_status_error():
    response = httpx.Response(
        200, content=b'not-json', headers={'Retry-After': '3'},
        request=httpx.Request('POST', 'https://example.test'),
    )
    transport = SystemOneTransport(
        url='https://example.test', api_key=None, model='test-model', timeout=2,
        http_client=SequenceClient([response]),
    )
    with pytest.raises(APIStatusError) as raised:
        transport.probe_once({})
    assert raised.value.usage_response.headers['Retry-After'] == '3'


def test_each_retry_has_its_own_ledger_row_and_keeps_partial_cost(temp_db):
    client = SequenceClient([
        _response(503, {'usage': {'input_tokens': 2, 'output_tokens': 1,
                                  'cost': 0.002}}),
        _response(200, {'model': 'test-model', 'usage': {
            'input_tokens': 3, 'output_tokens': 4, 'cost': 0.003},
            'answers': {}}),
    ])
    run_context.begin_llm_dispatch_context(_metadata())
    try:
        with patch('systemone.transport._sleep_retry', return_value=True):
            dispatch_systemone_request(
                {'model': 'test-model', 'questions': {}},
                url='https://example.test', api_key=None, model='test-model',
                timeout=2, request_kind='detection', http_client=client)
        first_attempt = run_context.first_llm_dispatch_attempt()
        Database().set_llm_call_latency(first_attempt, 123)
    finally:
        run_context.end_llm_dispatch_context()

    rows = temp_db.get_connection().execute(
        "SELECT state, input_tokens, output_tokens, cost_usd, "
        "dispatch_latency_ms, call_latency_ms FROM llm_call_usage ORDER BY rowid").fetchall()
    assert client.calls == 2
    assert [row['state'] for row in rows] == ['failure', 'success']
    assert [(row['input_tokens'], row['output_tokens']) for row in rows] == [(2, 1), (3, 4)]
    assert [row['cost_usd'] for row in rows] == ['0.002', '0.003']
    assert all(row['dispatch_latency_ms'] is not None for row in rows)
    assert [row['call_latency_ms'] for row in rows] == [123, None]


def test_deadline_limits_each_http_timeout(temp_db):
    client = SequenceClient([_response(200, {'answers': {}})])
    metadata = _metadata()
    metadata['deadline_at'] = time.monotonic() + 5
    run_context.begin_llm_dispatch_context(metadata)
    try:
        dispatch_systemone_request(
            {'model': 'test-model'}, url='https://example.test',
            api_key=None, model='test-model', timeout=30,
            request_kind='detection', http_client=client)
    finally:
        run_context.end_llm_dispatch_context()
    assert 0 < client.kwargs[0]['timeout'] <= 5


def test_cancel_before_send_does_not_reserve_or_post(temp_db):
    from cancel import ProcessingCancelled

    client = SequenceClient([])
    run_context.begin_llm_dispatch_context(_metadata())
    try:
        with patch('systemone.transport._check_cancel',
                   side_effect=ProcessingCancelled):
            with pytest.raises(ProcessingCancelled):
                dispatch_systemone_request(
                    {'model': 'test-model'}, url='https://example.test',
                    api_key=None, model='test-model', timeout=2,
                    request_kind='detection', http_client=client)
    finally:
        run_context.end_llm_dispatch_context()
    assert client.calls == 0
    assert temp_db.get_connection().execute(
        'SELECT COUNT(*) AS n FROM llm_call_usage').fetchone()['n'] == 0


def test_cap_crossed_after_failure_prevents_retry_send(temp_db):
    from rate_limit_hold import manual_rate_limit_caps

    client = SequenceClient([_response(503, {'error': 'temporary'})])
    caps = manual_rate_limit_caps('primary')
    caps.update(rpm=1, rpd=0, tpm=0)
    run_context.begin_llm_dispatch_context(_metadata())
    try:
        with patch('rate_limit_hold.manual_rate_limit_caps', return_value=caps), \
                patch('systemone.transport._sleep_retry', return_value=True):
            with pytest.raises(ProviderRateLimitedError):
                dispatch_systemone_request(
                    {'model': 'test-model'}, url='https://example.test',
                    api_key=None, model='test-model', timeout=2,
                    request_kind='detection', http_client=client)
    finally:
        run_context.end_llm_dispatch_context()
    assert client.calls == 1
    rows = temp_db.get_connection().execute(
        "SELECT state FROM llm_call_usage ORDER BY rowid").fetchall()
    assert [row['state'] for row in rows] == ['failure']


def test_outer_call_does_not_replay_per_request_adapter():
    class Client:
        uses_per_request_dispatch = True

        def __init__(self):
            self.calls = 0

        def messages_create(self, **kwargs):
            self.calls += 1
            raise _request_error(
                503, _response(503, {'error': 'down'}), {'error': 'down'})

    client = Client()
    response, error = call_llm(
        llm_client=client, model='m', system_prompt='s', prompt='p',
        llm_timeout=2, max_retries=2, max_tokens=32, slug=None,
        episode_id=None, call_label='test', phase_key='detection',
        provider='systemone-compatible')
    assert response is None
    assert error.status_code == 503
    assert client.calls == 1


def test_protocol_validation_failure_is_finalized_once(temp_db):
    client = SequenceClient([_response(200, {
        'usage': {'input_tokens': 7, 'output_tokens': 2, 'cost': 0.004},
        'answers': {},
    })])
    run_context.begin_llm_dispatch_context(_metadata())
    try:
        with pytest.raises(Exception, match='invalid protocol response'):
            dispatch_systemone_request(
                {'model': 'test-model'}, url='https://example.test',
                api_key=None, model='test-model', timeout=2,
                request_kind='detection', http_client=client,
                validate=lambda _body: (_ for _ in ()).throw(ValueError('invalid')))
    finally:
        run_context.end_llm_dispatch_context()

    row = temp_db.get_connection().execute(
        "SELECT state, input_tokens, output_tokens, cost_usd, dispatch_latency_ms "
        "FROM llm_call_usage").fetchone()
    assert client.calls == 1
    assert row['state'] == 'failure'
    assert row['input_tokens'] == 7
    assert row['output_tokens'] == 2
    assert row['cost_usd'] == '0.004'
    assert row['dispatch_latency_ms'] is not None


@pytest.mark.parametrize('exit_kind', ['success', 'late', 'validation', 'cancel', 'http'])
def test_each_wire_attempt_finalizes_and_accumulates_once(temp_db, exit_kind):
    ctx = run_context.begin('example-podcast', 'a1b2c3d4e5f6')
    ctx.tokens.start()
    metadata = _metadata()
    metadata['max_retries'] = 0
    body = {'usage': {'input_tokens': 7, 'output_tokens': 2, 'cost': 0.004}, 'answers': {}}
    status = 503 if exit_kind == 'http' else 200
    client = SequenceClient([_response(status, body)])
    original_post = client.post

    def post(*args, **kwargs):
        response = original_post(*args, **kwargs)
        if exit_kind == 'late':
            run_context.current_llm_dispatch_context()['deadline_at'] = time.monotonic() - 1
        if exit_kind == 'cancel':
            run_context.current_llm_dispatch_context()['cancel_check'] = lambda: (_ for _ in ()).throw(ProcessingCancelled())
        return response

    client.post = post
    deadline = time.monotonic() + 2
    if exit_kind == 'late':
        def post_late(*args, **kwargs):
            response = original_post(*args, **kwargs)
            time.sleep(0.025)
            return response
        client.post = post_late
        deadline = time.monotonic() + 0.02
    validate = (lambda _body: (_ for _ in ()).throw(JevReviewValidationError('answers_keys'))
                if exit_kind == 'validation' else None)
    run_context.begin_llm_dispatch_context(metadata)
    try:
        with patch.object(temp_db, 'finalize_llm_attempt_from_response',
                          wraps=temp_db.finalize_llm_attempt_from_response) as finalized:
            run_context.current_llm_dispatch_context()['database'] = temp_db
            if exit_kind == 'success':
                dispatch_systemone_request({}, url='https://example.test', api_key=None,
                                           model='test-model', timeout=2, request_kind='detection',
                                           http_client=client, deadline_at=deadline, validate=validate)
            else:
                with pytest.raises(Exception):
                    dispatch_systemone_request({}, url='https://example.test', api_key=None,
                                               model='test-model', timeout=2, request_kind='detection',
                                               http_client=client, deadline_at=deadline, validate=validate)
            assert finalized.call_count == 1
        assert ctx.tokens.collect_and_reset() == {'input_tokens': 7, 'output_tokens': 2, 'cost': 0.004}
        assert client.calls == 1
    finally:
        run_context.end_llm_dispatch_context()
        run_context.end(ctx)


def test_reservation_crossing_deadline_is_finalized_without_wire_or_usage(temp_db):
    metadata = _metadata()
    metadata['database'] = temp_db
    metadata['max_retries'] = 0
    reserve = metadata['reserve_request']

    def slow_reserve(*args, **kwargs):
        result = reserve(*args, **kwargs)
        time.sleep(0.025)
        return result

    metadata['reserve_request'] = slow_reserve
    client = SequenceClient([])
    run_context.begin_llm_dispatch_context(metadata)
    try:
        with patch.object(temp_db, 'finalize_llm_attempt_from_response',
                          wraps=temp_db.finalize_llm_attempt_from_response) as finalized:
            with pytest.raises(Exception):
                dispatch_systemone_request({}, url='https://example.test', api_key=None,
                                           model='test-model', timeout=2, request_kind='detection',
                                           http_client=client, deadline_at=time.monotonic() + 0.02)
            assert finalized.call_count == 1
    finally:
        run_context.end_llm_dispatch_context()
    row = temp_db.get_connection().execute('SELECT dispatch_count, state, input_tokens FROM llm_call_usage').fetchone()
    assert tuple(row) == (0, 'failure', None)
    assert client.calls == 0


@pytest.mark.parametrize('bad_tokens', ['bad', float('inf'), float('nan'), -1, True, 1.5, 2**63, float(2**63), 10**400])
def test_invalid_usage_fields_are_unknown_and_valid_partial_usage_is_finalized(temp_db, bad_tokens):
    metadata = _metadata()
    metadata['database'] = temp_db
    metadata['provider_key'] = 'typesafe'
    client = SequenceClient([httpx.Response(200, text=json.dumps(
        {'usage': {'input_tokens': bad_tokens, 'output_tokens': 2}}),
        request=httpx.Request('POST', 'https://example.test'))])
    run_context.begin_llm_dispatch_context(metadata)
    try:
        with pytest.raises(JevReviewValidationError) as raised:
            dispatch_systemone_request({}, url='https://example.test', api_key=None,
                                       model='jev-latest', timeout=2, request_kind='review',
                                       http_client=client,
                                       validate=lambda _body: (_ for _ in ()).throw(JevReviewValidationError('usage_input_tokens')))
        assert raised.value.rule == 'usage_input_tokens'
    finally:
        run_context.end_llm_dispatch_context()
    row = temp_db.get_connection().execute(
        'SELECT state, finalized_at, input_tokens, output_tokens, cost_usd FROM llm_call_usage').fetchone()
    assert row['state'] == 'failure' and row['finalized_at'] is not None
    assert row['input_tokens'] is None and row['output_tokens'] == 2 and row['cost_usd'] is None


def test_first_retry_hint_and_jitter_respect_native_cap_without_changing_chat():
    error = _request_error(429, _response(429, {}), {})
    error.response.headers['Retry-After'] = '60'
    with patch('utils.llm_call.random.uniform', return_value=2):
        assert _fallback_delay(error, 10, True, retry_after_cap=0) == 0
        assert _fallback_delay(error, 10, True, retry_after_cap=0.25) == 0.25
        assert _fallback_delay(error, 10, False, retry_after_cap=0.25) == 10
        assert _fallback_delay(error, 10, True) > 5


def test_last_request_slot_is_reserved_atomically_and_unsent_finalization_releases_it(temp_db):
    barrier = threading.Barrier(2)
    caps = {'rpm': 1, 'rpd': 0, 'tpm': 0, 'minute_since': '2000-01-01T00:00:00Z'}

    def reserve():
        barrier.wait(timeout=1)
        return temp_db.reserve_llm_attempt(
            run_id=None, podcast_id=None, episode_id=None, phase_key='detection',
            invoking_pass=None, provider_key='typesafe', configured_model='jev-latest', caps=caps, dispatch_count=0)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _item: reserve(), range(2)))
    accepted = [result for result in results if result['attempt_id']]
    assert len(accepted) == 1
    assert len([result for result in results if result['blocked'] == 'rpm']) == 1
    temp_db.finalize_llm_attempt_from_response(
        accepted[0]['attempt_id'], 'failure',
        DispatchResponse(usage={}, actual_dispatch_count=0))
    assert temp_db.count_recent_llm_attempts('typesafe', 'primary', caps['minute_since']) == 0
    assert temp_db._oldest_llm_attempt(temp_db.get_connection(), 'typesafe', 'primary', caps['minute_since']) is None
    assert temp_db.reserve_llm_attempt(
        run_id=None, podcast_id=None, episode_id=None, phase_key='detection',
        invoking_pass=None, provider_key='typesafe', configured_model='jev-latest', caps=caps)['attempt_id']


@pytest.mark.parametrize('bad_cost', ['bad', float('nan'), float('inf'), -1, True, 10**400])
@pytest.mark.parametrize('status', [200, 503])
def test_invalid_reported_cost_does_not_reject_valid_output_or_poison_finalization(temp_db, bad_cost, status):
    metadata = _metadata()
    metadata['database'] = temp_db
    body = {'answers': {}, 'usage': {'input_tokens': 7, 'output_tokens': 2, 'cost': bad_cost}}
    client = SequenceClient([httpx.Response(status, text=json.dumps(body),
                                           request=httpx.Request('POST', 'https://example.test'))])
    metadata['max_retries'] = 0
    ctx = run_context.begin('example-podcast', 'a1b2c3d4e5f6')
    ctx.tokens.start()
    run_context.begin_llm_dispatch_context(metadata)
    try:
        if status == 200:
            result = dispatch_systemone_request({}, url='https://example.test', api_key=None,
                                               model='test-model', timeout=2, request_kind='detection',
                                               http_client=client)
            assert result['answers'] == {}
        else:
            with pytest.raises(APIStatusError):
                dispatch_systemone_request({}, url='https://example.test', api_key=None,
                                           model='test-model', timeout=2, request_kind='detection',
                                           http_client=client)
        assert ctx.tokens.collect_and_reset() == {'input_tokens': 7, 'output_tokens': 2, 'cost': 0.0}
    finally:
        run_context.end_llm_dispatch_context()
        run_context.end(ctx)
    row = temp_db.get_connection().execute('SELECT state, input_tokens, output_tokens, cost_usd FROM llm_call_usage').fetchone()
    assert tuple(row) == ('success' if status == 200 else 'failure', 7, 2, None)


def test_typesafe_known_input_cost_survives_unknown_free_output_usage(temp_db):
    attempt = temp_db.begin_llm_attempt(run_id=None, podcast_id=None, episode_id=None,
                                       phase_key='detection', invoking_pass=None,
                                       provider_key='typesafe', configured_model='jev-latest')
    temp_db.finalize_llm_attempt_from_response(
        attempt, 'failure', DispatchResponse(usage={'input_tokens': 7, 'output_tokens': None}))
    row = temp_db.get_connection().execute('SELECT input_tokens, output_tokens, cost_usd, cost_source FROM llm_call_usage').fetchone()
    assert tuple(row) == (7, None, '2.94E-7', 'estimated')


def test_running_native_reservation_counts_for_quota_but_not_actual_http_stats(temp_db):
    temp_db.record_systemone_call_diagnostics(
        logical_call_id='reserved-native-operation', run_id=None, podcast_id=None,
        episode_id=None, provider_key='typesafe', credential_slot='primary',
        configured_model='jev-latest', phase_key='detection', window_label='window',
        logical_latency_ms=0, outcome='running')
    temp_db.reserve_llm_attempt(
        logical_call_id='reserved-native-operation', run_id=None, podcast_id=None,
        episode_id=None, phase_key='detection', invoking_pass=None,
        provider_key='typesafe', configured_model='jev-latest', dispatch_count=0)
    assert temp_db.count_recent_llm_attempts('typesafe', 'primary', '2000-01-01T00:00:00Z') == 1
    stats = temp_db.get_systemone_stats()
    assert stats['requests'] == 0
    assert stats['unknownCostRequestCount'] == 0
    assert stats['tokens']['unknownRequestCount'] == 0


def test_probe_dispatch_callback_crossing_deadline_does_not_send_or_fake_usage():
    client = SequenceClient([])
    transport = SystemOneTransport(url='https://example.test', api_key=None,
                                   model='jev-latest', timeout=2, http_client=client)
    with pytest.raises(Exception) as raised:
        transport.probe_once({}, deadline_at=time.monotonic() + 0.02,
                             on_dispatch=lambda: time.sleep(0.03))
    assert client.calls == 0
    assert raised.value.usage_response.actual_dispatch_count == 0
    assert raised.value.usage_response.usage == {}


@pytest.mark.parametrize('status', [200, 503])
def test_out_of_storage_range_tokens_remain_unknown_without_masking_outcome(temp_db, status):
    metadata = _metadata()
    metadata.update(database=temp_db, max_retries=0)
    body = {'answers': {}, 'usage': {'input_tokens': 2**63, 'output_tokens': 2}}
    client = SequenceClient([_response(status, body)])
    ctx = run_context.begin('example-podcast', 'a1b2c3d4e5f6')
    ctx.tokens.start()
    run_context.begin_llm_dispatch_context(metadata)
    try:
        with patch.object(temp_db, 'finalize_llm_attempt_from_response',
                          wraps=temp_db.finalize_llm_attempt_from_response) as finalized:
            if status == 200:
                assert dispatch_systemone_request({}, url='https://example.test', api_key=None,
                                                  model='test-model', timeout=2, request_kind='detection',
                                                  http_client=client)['answers'] == {}
            else:
                with pytest.raises(APIStatusError):
                    dispatch_systemone_request({}, url='https://example.test', api_key=None,
                                               model='test-model', timeout=2, request_kind='detection',
                                               http_client=client)
            assert finalized.call_count == 1
        assert ctx.tokens.collect_and_reset() == {'input_tokens': 0, 'output_tokens': 2, 'cost': 0.0}
    finally:
        run_context.end_llm_dispatch_context()
        run_context.end(ctx)
    row = temp_db.get_connection().execute(
        'SELECT state, finalized_at, input_tokens, output_tokens, cost_usd FROM llm_call_usage').fetchone()
    assert row['state'] == ('success' if status == 200 else 'failure')
    assert row['finalized_at'] is not None
    assert row['input_tokens'] is None and row['output_tokens'] == 2 and row['cost_usd'] is None
