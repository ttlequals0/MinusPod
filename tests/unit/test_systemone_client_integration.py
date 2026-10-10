"""Exercise the native client through the pipeline call and Jev adapter."""

import json
import threading
import time

import pytest
from concurrent.futures import ThreadPoolExecutor

import httpx

from tests.app_bootstrap import bootstrap

bootstrap('systemone_client_integration_')

import llm_client
import llm_route
import run_context
from ad_detector.prompts import format_window_prompt
from config import PROVIDER_SYSTEMONE_COMPATIBLE, PROVIDER_TYPESAFE
from systemone.admission import LocalOperationCapacityTimeout, operation_admission
from systemone.adapter import ReviewInconclusiveError, SystemOneSettings, _matched_sponsor_for_span
from systemone.protocol import JevReviewValidationError
from utils.llm_call import call_llm
from llm_client import is_review_inconclusive_error
from utils.circuit_breaker import CircuitBreaker
from sponsor_service import SponsorService, SEED_SPONSORS
from community_export import normalize_aliases
from tests.unit.test_systemone_adapter_review import (
    _meaningful_pair_prompt, build_review_prompt, make_text_fake,
)
from tests.unit.test_systemone_adapter_detection import _fake as detection_answer_fixture


class AnswerClient:
    def __init__(self, answerer=None, barrier=None):
        self.payloads = []
        self.timeouts = []
        self.answerer = answerer
        self.barrier = barrier

    def post(self, url, *, json, headers, timeout):
        self.payloads.append(json)
        self.timeouts.append((json['model'], timeout))
        if self.barrier:
            self.barrier.wait(timeout=5)
        if self.answerer:
            body = self.answerer(json)
        else:
            raise AssertionError('native HTTP fixture requires a source answer fixture')
        return httpx.Response(
            200, json=body, request=httpx.Request('POST', url),
        )

    def close(self):
        pass


def test_call_llm_returns_native_adapter_envelope_and_accounts_http_dispatch(
        temp_db, monkeypatch):
    temp_db.create_podcast('example-podcast', 'https://example.com/feed.xml', 'Example')
    ctx = run_context.begin('example-podcast', 'a1b2c3d4e5f6')
    fake_http = AnswerClient(detection_answer_fixture(set()))
    monkeypatch.setattr(
        llm_client, 'get_systemone_settings',
        lambda provider, slot, model: SystemOneSettings(
            model=model, category_pass=False, request_deadline_seconds=10.0),
    )
    monkeypatch.setattr(
        llm_client.SystemOneClient, '_get_http_client',
        lambda self, timeout: fake_http,
    )
    client = llm_client.SystemOneClient(
        PROVIDER_TYPESAFE, base_url='https://api.typesafe.ai/v1', api_key=None)
    prompt = format_window_prompt(
        'Example', 'Episode', '', ['[0.0s - 6.0s] We hiked in the mountains.'],
        0, 1, 0.0, 6.0,
    )
    try:
        response, error = call_llm(
            llm_client=client, model='jev-latest', system_prompt='Detection policy.',
            prompt=prompt, llm_timeout=30, max_retries=0, max_tokens=200,
            slug='example-podcast', episode_id='a1b2c3d4e5f6',
            call_label='window 1', phase_key='detection',
            pass_name='ad_detection_pass_1', provider=PROVIDER_TYPESAFE,
            response_format={'type': 'json_object'},
        )
    finally:
        run_context.end(ctx)

    assert error is None
    assert json.loads(response.content) == {'ads': []}
    assert response.usage == {'input_tokens': 1000, 'output_tokens': 5}
    assert len(fake_http.payloads) == 1
    assert 'response_format' not in fake_http.payloads[0]
    row = temp_db.get_connection().execute(
        'SELECT state, dispatch_count, input_tokens, output_tokens, call_latency_ms '
        'FROM llm_call_usage WHERE episode_id = ?', ('a1b2c3d4e5f6',),
    ).fetchone()
    assert tuple(row[:4]) == ('success', 1, 1000, 5)
    assert row['call_latency_ms'] is not None
    logical = temp_db.get_connection().execute(
        'SELECT logical_call_id, outcome, logical_latency_ms, diagnostics_json '
        'FROM systemone_call_diagnostics WHERE episode_id = ?',
        ('a1b2c3d4e5f6',),
    ).fetchone()
    assert logical['logical_call_id']
    assert logical['outcome'] == 'completed'
    assert json.loads(logical['diagnostics_json'])['outcome'] == 'completed'
    assert logical['logical_latency_ms'] > 0
    summary = temp_db.get_systemone_stats(provider=PROVIDER_TYPESAFE)
    assert (summary['calls'], summary['requests']) == (1, 1)
    assert summary['requests'] == 1
    assert summary['logicalLatencyMsTotal'] > 0
    assert temp_db.delete_podcast('example-podcast')
    assert temp_db.get_connection().execute(
        'SELECT COUNT(*) FROM systemone_call_diagnostics').fetchone()[0] == 0


def test_call_llm_runs_review_through_native_protocol_and_extracts_envelope(
        temp_db, monkeypatch):
    temp_db.create_podcast('example-podcast', 'https://example.com/feed.xml', 'Example')
    ctx = run_context.begin('example-podcast', 'a1b2c3d4e5f6')
    fake_http = AnswerClient(make_text_fake())
    monkeypatch.setattr(
        llm_client, 'get_systemone_settings',
        lambda provider, slot, model: SystemOneSettings(
            model=model, category_pass=False, request_deadline_seconds=10.0),
    )
    monkeypatch.setattr(
        llm_client.SystemOneClient, '_get_http_client',
        lambda self, timeout: fake_http,
    )
    client = llm_client.SystemOneClient(
        PROVIDER_TYPESAFE, base_url='https://api.typesafe.ai/v1', api_key=None)
    prompt = build_review_prompt(
        100.0, 120.0, [(90.0, 100.0, 'Discussion ends.')],
        [(100.0, 120.0, 'This episode is sponsored by Acme.')],
        [(120.0, 130.0, 'Welcome back to the show.')],
    )
    try:
        response, error = call_llm(
            llm_client=client, model='jev-latest', system_prompt='Review policy.',
            prompt=prompt, llm_timeout=30, max_retries=0, max_tokens=200,
            slug='example-podcast', episode_id='a1b2c3d4e5f6',
            call_label='review', phase_key='review',
            pass_name='reviewer_pass_1', provider=PROVIDER_TYPESAFE,
        )
    finally:
        run_context.end(ctx)

    assert error is None
    payload = json.loads(response.content)
    assert payload == {
        'ads': [{
            'confidence': 0.98, 'end': 120.0, 'is_ad': True,
            'reason': 'Based on transcript: This episode is sponsored by Acme.',
            'start': 100.0,
        }],
    }
    assert fake_http.payloads
    row = temp_db.get_connection().execute(
        "SELECT state, phase_key, dispatch_count FROM llm_call_usage "
        "WHERE episode_id = ?", ('a1b2c3d4e5f6',),
    ).fetchall()
    assert len(row) == len(fake_http.payloads)
    assert all(item['state'] == 'success' and item['phase_key'] == 'review'
               for item in row)


def test_concurrent_calls_keep_model_settings_and_transport_local(temp_db, monkeypatch):
    temp_db.create_podcast('example-podcast', 'https://example.com/feed.xml', 'Example')
    fake_http = AnswerClient(
        detection_answer_fixture(set()), barrier=threading.Barrier(2))
    monkeypatch.setattr(
        llm_client, 'get_systemone_settings',
        lambda provider, slot, model: SystemOneSettings(
            model=model, category_pass=False,
            request_timeout=3.0 if model == 'jev-latest' else 7.0,
            request_deadline_seconds=10.0),
    )
    monkeypatch.setattr(
        llm_client.SystemOneClient, '_get_http_client',
        lambda self, timeout: fake_http,
    )
    client = llm_client.SystemOneClient(
        PROVIDER_TYPESAFE, base_url='https://api.typesafe.ai/v1', api_key=None)
    prompt = format_window_prompt(
        'Example', 'Episode', '', ['[0.0s - 6.0s] We hiked in the mountains.'],
        0, 1, 0.0, 6.0,
    )

    def invoke(model, episode_id):
        ctx = run_context.begin('example-podcast', episode_id)
        try:
            return call_llm(
                llm_client=client, model=model, system_prompt='Detection policy.',
                prompt=prompt, llm_timeout=30, max_retries=0, max_tokens=200,
                slug='example-podcast', episode_id=episode_id,
                call_label='window 1', phase_key='detection',
                pass_name='ad_detection_pass_1', provider=PROVIDER_TYPESAFE,
            )
        finally:
            run_context.end(ctx)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(
            lambda values: invoke(*values),
            [('jev-latest', 'episode-latest'), ('jev-preview', 'episode-preview')],
        ))

    assert all(error is None and json.loads(response.content) == {'ads': []}
               for response, error in results)
    assert set(fake_http.timeouts) == {('jev-latest', 3.0), ('jev-preview', 7.0)}
    rows = temp_db.get_connection().execute(
        'SELECT configured_model, state FROM llm_call_usage WHERE episode_id IN (?, ?)',
        ('episode-latest', 'episode-preview'),
    ).fetchall()
    assert {(row['configured_model'], row['state']) for row in rows} == {
        ('jev-latest', 'success'), ('jev-preview', 'success'),
    }


def test_inconclusive_upstream_diagnostics_survive_call_llm(temp_db, monkeypatch):
    temp_db.create_podcast('example-podcast', 'https://example.com/feed.xml', 'Example')
    ctx = run_context.begin('example-podcast', 'a1b2c3d4e5f6')

    class InconclusiveClient(AnswerClient):
        def post(self, url, *, json, headers, timeout):
            self.payloads.append(json)
            body = {'error': {
                'code': 'jev_review_inconclusive',
                'reason': 'proposed_range_not_confirmed',
                'stage': 'focused_validation',
                'score': 0.42,
                'threshold': 0.65,
                'cache_hit': False,
                'range_start': 101.0,
                'range_end': 119.0,
                'candidate_start': 100.0,
                'candidate_end': 120.0,
                'context_start': 90.0,
                'context_end': 130.0,
                'proposal': {
                    'reason': 'proposed_range_not_confirmed',
                    'stage': 'focused_validation', 'range_start': 101.0,
                    'range_end': 119.0, 'score': 0.42, 'threshold': 0.65,
                    'cache_hit': False,
                },
            }}
            return httpx.Response(
                422, json=body, request=httpx.Request('POST', url),
            )

    fake_http = InconclusiveClient()
    monkeypatch.setattr(
        llm_client, 'get_systemone_settings',
        lambda provider, slot, model: SystemOneSettings(
            model=model, request_deadline_seconds=10.0),
    )
    monkeypatch.setattr(
        llm_client.SystemOneClient, '_get_http_client',
        lambda self, timeout: fake_http,
    )
    client = llm_client.SystemOneClient(
        PROVIDER_TYPESAFE, base_url='https://api.typesafe.ai/v1', api_key=None)
    prompt = build_review_prompt(
        100.0, 120.0, [(90.0, 100.0, 'Discussion ends.')],
        [(100.0, 120.0, 'This episode is sponsored by Acme.')],
        [(120.0, 130.0, 'Welcome back to the show.')],
    )
    try:
        response, error = call_llm(
            llm_client=client, model='jev-latest', system_prompt='Review policy.',
            prompt=prompt, llm_timeout=30, max_retries=0, max_tokens=200,
            slug='example-podcast', episode_id='a1b2c3d4e5f6',
            call_label='review', phase_key='review', pass_name='reviewer_pass_1',
            provider=PROVIDER_TYPESAFE,
        )
    finally:
        run_context.end(ctx)

    assert response is None
    assert isinstance(error, ReviewInconclusiveError)
    assert is_review_inconclusive_error(error)
    assert (error.reason, error.stage, error.score, error.threshold) == (
        'proposed_range_not_confirmed', 'focused_validation', 0.42, 0.65)
    assert (error.candidate_start, error.candidate_end,
            error.context_start, error.context_end) == (100.0, 120.0, 90.0, 130.0)
    assert error.proposal['range_start'] == 101.0
    row = temp_db.get_connection().execute(
        'SELECT state, input_tokens, output_tokens FROM llm_call_usage '
        'WHERE episode_id = ?', ('a1b2c3d4e5f6',),
    ).fetchone()
    assert row['state'] == 'inconclusive'
    diagnostic = temp_db.get_connection().execute(
        'SELECT outcome, reason, stage, diagnostics_json FROM systemone_call_diagnostics '
        'WHERE episode_id = ?', ('a1b2c3d4e5f6',),
    ).fetchone()
    assert tuple(diagnostic[:3]) == (
        'inconclusive', 'proposed_range_not_confirmed', 'focused_validation')
    safe = json.loads(diagnostic['diagnostics_json'])['error_details']
    assert safe['score'] == 0.42
    assert safe['threshold'] == 0.65
    assert safe['candidate_start'] == 100.0
    assert 'This episode is sponsored' not in diagnostic['diagnostics_json']


def test_local_setup_failure_releases_half_open_probe(temp_db, monkeypatch):
    monkeypatch.setattr(
        llm_client, 'get_systemone_settings',
        lambda provider, slot, model: SystemOneSettings(model=model),
    )
    client = llm_client.SystemOneClient(
        PROVIDER_TYPESAFE, base_url='https://api.typesafe.ai/v1', api_key=None)
    breaker = CircuitBreaker('systemone', failure_threshold=1, recovery_timeout=0)
    breaker.record_failure(RuntimeError('previous upstream failure'))
    client.set_circuit_breaker(breaker)
    run_context.begin_llm_dispatch_context({'database': temp_db})
    monkeypatch.setattr(
        llm_client.SponsorService, 'get_sponsors',
        lambda _self: (_ for _ in ()).throw(RuntimeError('local sponsor lookup failed')),
    )

    try:
        try:
            client.messages_create(
                model='jev-latest', max_tokens=20, system='sys',
                messages=[{'role': 'user', 'content': 'hello'}],
            )
        except RuntimeError as error:
            assert str(error) == 'local sponsor lookup failed'
        else:
            raise AssertionError('expected local sponsor lookup failure')
    finally:
        run_context.end_llm_dispatch_context()

    assert breaker.state == CircuitBreaker.HALF_OPEN
    assert breaker.check() is not None
    client._release_circuit_breaker_probe()


def test_invalid_review_response_keeps_source_rule_diagnostics(temp_db, monkeypatch):
    temp_db.create_podcast('example-podcast', 'https://example.com/feed.xml', 'Example')
    ctx = run_context.begin('example-podcast', 'a1b2c3d4e5f6')
    fake_http = AnswerClient(lambda _payload: {
        'answers': {}, 'usage': {'input_tokens': 17, 'output_tokens': 0},
    })
    monkeypatch.setattr(
        llm_client, 'get_systemone_settings',
        lambda provider, slot, model: SystemOneSettings(
            model=model, request_deadline_seconds=10.0),
    )
    monkeypatch.setattr(
        llm_client.SystemOneClient, '_get_http_client',
        lambda self, timeout: fake_http,
    )
    client = llm_client.SystemOneClient(
        PROVIDER_TYPESAFE, base_url='https://api.typesafe.ai/v1', api_key=None)
    prompt = build_review_prompt(
        100.0, 120.0, [(90.0, 100.0, 'Discussion ends.')],
        [(100.0, 120.0, 'This episode is sponsored by Acme.')],
        [(120.0, 130.0, 'Welcome back to the show.')],
    )
    try:
        response, error = call_llm(
            llm_client=client, model='jev-latest', system_prompt='Review policy.',
            prompt=prompt, llm_timeout=30, max_retries=0, max_tokens=200,
            slug='example-podcast', episode_id='a1b2c3d4e5f6',
            call_label='review', phase_key='review', pass_name='reviewer_pass_1',
            provider=PROVIDER_TYPESAFE,
        )
    finally:
        run_context.end(ctx)

    assert response is None
    assert isinstance(error.__cause__, JevReviewValidationError)
    assert error.__cause__.rule == 'answers_keys'
    assert error.__cause__.numeric_details == {'expected_count': 3, 'actual_count': 0}
    row = temp_db.get_connection().execute(
        'SELECT state, input_tokens, output_tokens FROM llm_call_usage '
        'WHERE episode_id = ?', ('a1b2c3d4e5f6',),
    ).fetchone()
    assert tuple(row) == ('failure', 17, 0)
    diagnostic = temp_db.get_connection().execute(
        'SELECT reason, diagnostics_json FROM systemone_call_diagnostics '
        'WHERE episode_id = ?', ('a1b2c3d4e5f6',)).fetchone()
    assert diagnostic['reason'] == 'answers_keys'
    assert json.loads(diagnostic['diagnostics_json'])['error_details'] == {
        'validation_rule': 'answers_keys',
        'numeric_details': {'expected_count': 3, 'actual_count': 0},
    }


def test_pre_dispatch_inconclusive_is_persisted_without_request_row(temp_db, monkeypatch):
    temp_db.create_podcast('example-podcast', 'https://example.com/feed.xml', 'Example')
    ctx = run_context.begin('example-podcast', 'a1b2c3d4e5f6')
    fake_http = AnswerClient(lambda _payload: (_ for _ in ()).throw(
        AssertionError('pre-dispatch inconclusive must not send')))
    monkeypatch.setattr(
        llm_client, 'get_systemone_settings',
        lambda provider, slot, model: SystemOneSettings(
            model=model, max_choice_options=1, request_deadline_seconds=10.0),
    )
    monkeypatch.setattr(
        llm_client.SystemOneClient, '_get_http_client',
        lambda self, timeout: fake_http,
    )
    client = llm_client.SystemOneClient(
        PROVIDER_TYPESAFE, base_url='https://api.typesafe.ai/v1', api_key=None)
    prompt = _meaningful_pair_prompt().replace(
        '\nTranscript (60s before, the candidate ad, 60s after;',
        '\nEffective category actions: sponsor=remove\n'
        'Effective category actions: sponsor=remove\n\n'
        'Transcript (60s before, the candidate ad, 60s after;',
    )
    try:
        response, error = call_llm(
            llm_client=client, model='jev-latest', system_prompt='Review policy.',
            prompt=prompt, llm_timeout=30, max_retries=0, max_tokens=200,
            slug='example-podcast', episode_id='a1b2c3d4e5f6',
            call_label='review', phase_key='review', pass_name='reviewer_pass_1',
            provider=PROVIDER_TYPESAFE,
        )
    finally:
        run_context.end(ctx)

    assert response is None
    assert isinstance(error, ReviewInconclusiveError)
    assert error.reason == 'policy_conflict'
    assert fake_http.payloads == []
    assert temp_db.get_connection().execute(
        'SELECT COUNT(*) FROM llm_call_usage WHERE episode_id = ?',
        ('a1b2c3d4e5f6',),
    ).fetchone()[0] == 0
    summary = temp_db.get_systemone_stats(provider=PROVIDER_TYPESAFE)
    assert (summary['calls'], summary['requests']) == (1, 0)
    assert summary['outcomes']['inconclusive'] == 1


def test_account_change_after_response_finalizes_usage_without_retry(temp_db, monkeypatch):
    temp_db.create_podcast('example-podcast', 'https://example.com/feed.xml', 'Example')
    ctx = run_context.begin('example-podcast', 'a1b2c3d4e5f6')
    fake_http = AnswerClient(detection_answer_fixture(set()))
    account_checks = 0

    def route_account_mismatch(_route):
        nonlocal account_checks
        account_checks += 1
        return ('account-a', 'account-b') if fake_http.payloads else None

    monkeypatch.setattr(
        llm_client, 'get_systemone_settings',
        lambda provider, slot, model: SystemOneSettings(
            model=model, category_pass=False, request_deadline_seconds=10.0),
    )
    monkeypatch.setattr(
        llm_client.SystemOneClient, '_get_http_client',
        lambda self, timeout: fake_http,
    )
    monkeypatch.setattr(run_context, 'route_for_phase', lambda _phase: {'provider': 'typesafe'})
    monkeypatch.setattr(llm_route, 'route_account_mismatch', route_account_mismatch)
    client = llm_client.SystemOneClient(
        PROVIDER_TYPESAFE, base_url='https://api.typesafe.ai/v1', api_key=None)
    prompt = format_window_prompt(
        'Example', 'Episode', '', ['[0.0s - 6.0s] We hiked in the mountains.'],
        0, 1, 0.0, 6.0,
    )
    try:
        response, error = call_llm(
            llm_client=client, model='jev-latest', system_prompt='Detection policy.',
            prompt=prompt, llm_timeout=30, max_retries=0, max_tokens=200,
            slug='example-podcast', episode_id='a1b2c3d4e5f6',
            call_label='window 1', phase_key='detection',
            pass_name='ad_detection_pass_1', provider=PROVIDER_TYPESAFE,
        )
    finally:
        run_context.end(ctx)

    assert response is None
    assert isinstance(error, llm_client.ProviderAccountChangedError)
    assert len(fake_http.payloads) == 1
    row = temp_db.get_connection().execute(
        'SELECT state, input_tokens, output_tokens FROM llm_call_usage '
        'WHERE episode_id = ?', ('a1b2c3d4e5f6',),
    ).fetchone()
    assert tuple(row) == ('failure', 1000, 5)


def test_compatible_endpoint_resolution_matches_client_cache_and_account_route(
        monkeypatch):
    settings = {'systemone_base_url': 'https://db.example/v1'}
    monkeypatch.setattr(llm_client, '_get_cached_setting', lambda key: settings.get(key))
    monkeypatch.setenv('SYSTEMONE_BASE_URL', 'https://env.example/v1')

    primary = llm_client._build_client(PROVIDER_SYSTEMONE_COMPATIBLE)
    assert primary.base_url == 'https://db.example/v1'
    assert llm_client._resolve_cache_key(PROVIDER_SYSTEMONE_COMPATIBLE)[1] == primary.base_url
    assert llm_route._base_url_for_primary(PROVIDER_SYSTEMONE_COMPATIBLE) == primary.base_url

    settings.pop('systemone_base_url')
    env_primary = llm_client._build_client(PROVIDER_SYSTEMONE_COMPATIBLE)
    assert env_primary.base_url == 'https://env.example/v1'
    assert llm_client._resolve_cache_key(PROVIDER_SYSTEMONE_COMPATIBLE)[1] == env_primary.base_url
    assert llm_route._base_url_for_primary(PROVIDER_SYSTEMONE_COMPATIBLE) == env_primary.base_url

    with pytest.raises(ValueError, match='requires a base URL'):
        llm_client._build_client(PROVIDER_SYSTEMONE_COMPATIBLE, credential_slot='secondary')
    primary.close()
    env_primary.close()


@pytest.mark.parametrize('slot', ['primary', 'secondary'])
@pytest.mark.parametrize('provider', [PROVIDER_TYPESAFE, PROVIDER_SYSTEMONE_COMPATIBLE])
def test_local_capacity_timeout_has_no_provider_or_accounting_side_effects(temp_db, monkeypatch, slot, provider):
    fake_http = AnswerClient(detection_answer_fixture(set()))
    monkeypatch.setattr(llm_client, 'get_systemone_settings', lambda *_args: SystemOneSettings(
        model='jev-latest', category_pass=False, request_deadline_seconds=0.03,
        max_concurrent_operations=4))
    monkeypatch.setattr(llm_client.SystemOneClient, '_get_http_client', lambda *_args: fake_http)
    client = llm_client.SystemOneClient(provider,
                                      base_url='https://api.typesafe.ai/v1', api_key=None)
    client.credential_slot = slot
    breaker = CircuitBreaker('systemone-capacity', failure_threshold=1, recovery_timeout=0)
    client.set_circuit_breaker(breaker)
    with operation_admission('https://api.typesafe.ai/v1/systemone', None, 1,
                             deadline_at=time.monotonic() + 2):
        response, error = call_llm(
            llm_client=client, model='jev-latest', system_prompt='Detection policy.',
            prompt='hello', llm_timeout=30, max_retries=2, max_tokens=200,
            call_label='capacity', phase_key='detection', provider=provider,
            slug=None, episode_id=None,
            credential_slot=slot)
    assert response is None
    assert isinstance(error, LocalOperationCapacityTimeout)
    assert not llm_client.is_retryable_error(error)
    assert not llm_client.is_failover_trigger_error(error)
    assert fake_http.payloads == []
    assert breaker.state == CircuitBreaker.CLOSED
    assert temp_db.get_connection().execute('SELECT COUNT(*) FROM llm_call_usage').fetchone()[0] == 0
    row = temp_db.get_connection().execute('SELECT outcome, reason, stage FROM systemone_call_diagnostics').fetchone()
    assert tuple(row) == ('failed', 'local_operation_capacity_timeout', 'admission')


def test_queued_same_endpoint_key_rotation_aborts_without_dispatch(temp_db, monkeypatch):
    fake_http = AnswerClient(detection_answer_fixture(set()))
    queued = threading.Event()
    monkeypatch.setenv('TYPESAFE_API_KEY', 'before-rotation')
    monkeypatch.setattr(llm_client, 'get_systemone_settings', lambda *_args: SystemOneSettings(
        model='jev-latest', request_deadline_seconds=2, max_concurrent_operations=1))
    monkeypatch.setattr(llm_client.SystemOneClient, '_get_http_client', lambda *_args: fake_http)
    original_check = llm_client.SystemOneClient._admission_check

    def make_check(self, metadata):
        check = original_check(self, metadata)
        def observed(waited):
            check(waited)
            if waited:
                queued.set()
        return observed

    monkeypatch.setattr(llm_client.SystemOneClient, '_admission_check', make_check)
    client = llm_client.SystemOneClient(PROVIDER_TYPESAFE,
                                      base_url='https://api.typesafe.ai/v1', api_key='before-rotation')
    with ThreadPoolExecutor(max_workers=1) as executor:
        with operation_admission('https://api.typesafe.ai/v1/systemone', 'before-rotation', 1,
                                 deadline_at=time.monotonic() + 3):
            future = executor.submit(client.messages_create, 'jev-latest', 200, 'policy',
                                     [{'role': 'user', 'content': 'hello'}])
            assert queued.wait(1)
            monkeypatch.setenv('TYPESAFE_API_KEY', 'after-rotation')
            with pytest.raises(llm_client.ProviderAccountChangedError):
                future.result(timeout=1)
    assert fake_http.payloads == []
    assert temp_db.get_connection().execute('SELECT COUNT(*) FROM llm_call_usage').fetchone()[0] == 0
    with operation_admission('https://api.typesafe.ai/v1/systemone', 'before-rotation', 1,
                             deadline_at=time.monotonic() + 1):
        pass


def test_concurrent_native_calls_accumulate_into_shared_detector_run(temp_db, monkeypatch):
    temp_db.create_podcast('example-podcast', 'https://example.com/feed.xml', 'Example')
    fake_http = AnswerClient(detection_answer_fixture(set()), barrier=threading.Barrier(2))
    monkeypatch.setattr(llm_client, 'get_systemone_settings', lambda *_args: SystemOneSettings(
        model='jev-latest', category_pass=False, request_deadline_seconds=5))
    monkeypatch.setattr(llm_client.SystemOneClient, '_get_http_client', lambda *_args: fake_http)
    client = llm_client.SystemOneClient(PROVIDER_TYPESAFE,
                                      base_url='https://api.typesafe.ai/v1', api_key=None)
    prompt = format_window_prompt('Example', 'Episode', '',
                                 ['[0.0s - 6.0s] We hiked in the mountains.'], 0, 1, 0.0, 6.0)
    ctx = run_context.begin('example-podcast', 'a1b2c3d4e5f6')
    ctx.tokens.start()

    def invoke():
        return call_llm(llm_client=client, model='jev-latest', system_prompt='Detection policy.',
                        prompt=prompt, llm_timeout=30, max_retries=0, max_tokens=200,
                        slug='example-podcast', episode_id='a1b2c3d4e5f6',
                        call_label='window', phase_key='detection', provider=PROVIDER_TYPESAFE)

    try:
        bound = run_context.run_in_worker_thread(invoke)
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(bound) for _ in range(2)]
            results = [future.result(timeout=3) for future in futures]
        assert all(error is None for _response, error in results)
        assert ctx.tokens.collect_and_reset() == {
            'input_tokens': 2000, 'output_tokens': 10, 'cost': 0.000084}
    finally:
        run_context.end(ctx)
    assert len(fake_http.payloads) == 2
    rows = temp_db.get_connection().execute('SELECT state, dispatch_count FROM llm_call_usage').fetchall()
    assert [tuple(row) for row in rows] == [('success', 1), ('success', 1)]


def test_native_composition_snapshots_source_seed_names_and_exact_aliases(temp_db, monkeypatch):
    service = SponsorService(temp_db)
    service.seed_initial_data()
    expected = {(row['name'], term) for row in SEED_SPONSORS
                for term in [row['name'], *(row.get('aliases') or [])]}
    captured = []
    original = llm_client.run_chat_completion

    def observed(**kwargs):
        captured.extend(kwargs['sponsor_lookup']())
        return original(**kwargs)

    monkeypatch.setattr(llm_client, 'run_chat_completion', observed)
    monkeypatch.setattr(llm_client, 'get_systemone_settings', lambda *_args: SystemOneSettings(
        model='jev-latest', category_pass=False))
    fake_http = AnswerClient(detection_answer_fixture(set()))
    monkeypatch.setattr(llm_client.SystemOneClient, '_get_http_client', lambda *_args: fake_http)
    client = llm_client.SystemOneClient(PROVIDER_TYPESAFE,
                                      base_url='https://api.typesafe.ai/v1', api_key=None)
    prompt = format_window_prompt('Example', 'Episode', '',
                                 ['[0.0s - 6.0s] We hiked in the mountains.'], 0, 1, 0.0, 6.0)
    response, error = call_llm(llm_client=client, model='jev-latest', system_prompt='policy',
                               prompt=prompt, llm_timeout=30, max_retries=0, max_tokens=200,
                               call_label='seed', phase_key='detection', provider=PROVIDER_TYPESAFE,
                               slug=None, episode_id=None)
    assert error is None and response is not None
    actual = {(row['name'], term) for row in captured for term in row['candidates']}
    assert expected <= actual
    assert actual == {(row['name'], term) for row in service.get_sponsors()
                      for term in [row['name'], *normalize_aliases(row.get('aliases'))]}

    inactive = SEED_SPONSORS[0]['name']
    inactive_row = temp_db.get_known_sponsor_by_name(inactive)
    temp_db.update_known_sponsor(inactive_row['id'], is_active=0)
    service.seed_initial_data()
    captured.clear()
    response, error = call_llm(llm_client=client, model='jev-latest', system_prompt='policy',
                               prompt=prompt, llm_timeout=30, max_retries=0, max_tokens=200,
                               call_label='inactive-seed', phase_key='detection', provider=PROVIDER_TYPESAFE,
                               slug=None, episode_id=None)
    assert error is None and response is not None
    assert inactive not in {row['name'] for row in captured}


def test_probe_waits_for_whole_operation_admission_before_reserving(temp_db, monkeypatch):
    fake_http = AnswerClient(detection_answer_fixture(set()))
    monkeypatch.setattr(llm_client, 'get_systemone_settings', lambda *_args: SystemOneSettings(
        model='jev-latest', request_deadline_seconds=0.03, max_concurrent_operations=4))
    monkeypatch.setattr(llm_client.SystemOneClient, '_get_http_client', lambda *_args: fake_http)
    client = llm_client.SystemOneClient(PROVIDER_TYPESAFE,
                                      base_url='https://api.typesafe.ai/v1', api_key=None)
    reserved = []
    with operation_admission('https://api.typesafe.ai/v1/systemone', None, 1,
                             deadline_at=time.monotonic() + 2):
        with pytest.raises(LocalOperationCapacityTimeout):
            client.probe_once('jev-latest', reserve_request=lambda payload: reserved.append(payload))
    assert reserved == [] and fake_http.payloads == []


def test_probe_reservation_expiry_is_unsent_and_finalized_without_breaker_failure(temp_db, monkeypatch):
    fake_http = AnswerClient(detection_answer_fixture(set()))
    monkeypatch.setattr(llm_client, 'get_systemone_settings', lambda *_args: SystemOneSettings(
        model='jev-latest', request_deadline_seconds=0.02))
    monkeypatch.setattr(llm_client.SystemOneClient, '_get_http_client', lambda *_args: fake_http)
    client = llm_client.SystemOneClient(PROVIDER_TYPESAFE,
                                      base_url='https://api.typesafe.ai/v1', api_key=None)
    breaker = CircuitBreaker('systemone-unsent-probe', failure_threshold=1, recovery_timeout=0)
    client.set_circuit_breaker(breaker)

    def reserve(_payload):
        attempt = temp_db.begin_llm_attempt(run_id=None, podcast_id=None, episode_id=None,
                                           phase_key='probe', invoking_pass=None,
                                           provider_key='typesafe', configured_model='jev-latest')
        time.sleep(0.03)
        return attempt

    with pytest.raises(Exception) as raised:
        client.probe_once('jev-latest', reserve_request=reserve)
    attempts = raised.value.systemone_probe_attempts
    assert len(attempts) == 1
    attempt_id, response, latency = attempts[0]
    temp_db.finalize_llm_attempt_from_response(attempt_id, 'failure', response, dispatch_latency_ms=latency)
    row = temp_db.get_connection().execute('SELECT dispatch_count, state FROM llm_call_usage').fetchone()
    assert tuple(row) == (0, 'failure')
    assert fake_http.payloads == [] and breaker.state == CircuitBreaker.CLOSED


@pytest.mark.parametrize('prefix', ['jev-', 'sys1-'])
def test_generic_sponsor_names_are_excluded_from_native_grounding(prefix):
    seed = SEED_SPONSORS[0]
    name = prefix + seed['name']
    assert _matched_sponsor_for_span('This episode is sponsored by ' + name,
                                     lambda: ({'name': name, 'candidates': (name,)},)) is None

def test_probe_dispatch_mark_crossing_deadline_has_no_dispatch_latency_or_http_stats(temp_db, monkeypatch):
    fake_http = AnswerClient(detection_answer_fixture(set()))
    monkeypatch.setattr(llm_client, 'get_systemone_settings', lambda *_args: SystemOneSettings(
        model='jev-latest', request_deadline_seconds=0.02))
    monkeypatch.setattr(llm_client.SystemOneClient, '_get_http_client', lambda *_args: fake_http)
    client = llm_client.SystemOneClient(PROVIDER_TYPESAFE,
                                      base_url='https://api.typesafe.ai/v1', api_key=None)

    def reserve(_payload):
        return temp_db.reserve_llm_attempt(run_id=None, podcast_id=None, episode_id=None,
                                          phase_key='probe', invoking_pass=None,
                                          provider_key='typesafe', configured_model='jev-latest',
                                          dispatch_count=0)['attempt_id']

    def mark(attempt_id):
        temp_db.bump_llm_attempt_dispatch_count(attempt_id)
        time.sleep(0.03)

    with pytest.raises(Exception) as raised:
        client.probe_once('jev-latest', reserve_request=reserve, note_dispatch=mark)
    attempt_id, response, latency = raised.value.systemone_probe_attempts[0]
    assert response.actual_dispatch_count == 0 and latency is None
    temp_db.finalize_llm_attempt_from_response(attempt_id, 'failure', response, dispatch_latency_ms=latency)
    row = temp_db.get_connection().execute('SELECT dispatch_count, dispatch_latency_ms FROM llm_call_usage').fetchone()
    assert tuple(row) == (0, None)
    assert temp_db.get_systemone_stats()['requests'] == 0
    assert fake_http.payloads == []
