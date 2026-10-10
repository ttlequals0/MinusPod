"""Native benchmark dispatch uses source fixtures without inference."""
import asyncio
import ast
from pathlib import Path
import json
import threading
from decimal import Decimal

import httpx
import pytest

from benchmark import config, llm, runner, systemone
from benchmark.storage import read_jsonl
from tests.test_jev import contiguous


def _provider(kind='typesafe', **settings):
    return config.ProviderConfig(name='native', client=kind, api_key_env=None,
                                 base_url='https://example.com/v1',
                                 systemone={'category_pass': False, 'max_questions_per_request': 1,
                                            **settings})


def _prompt():
    lines = [f"[{seg['start']:.1f}s - {seg['end']:.1f}s] {seg['text']}" for seg in contiguous(2)]
    return 'Podcast: Example\nEpisode title: Example\nTranscript:\n' + '\n'.join(lines)


def _answer(payload, *, usage=True):
    response = {'answers': {key: {'noul': 0.9} for key in payload['questions']}}
    if usage:
        response['usage'] = {'input_tokens': 1000, 'output_tokens': 5}
    return response


def _http(monkeypatch, handler):
    real_client = httpx.Client
    monkeypatch.setattr(systemone.httpx, 'Client', lambda **kwargs: real_client(
        transport=httpx.MockTransport(handler), **kwargs))


def _call(provider):
    return asyncio.run(llm.call_with_retry(
        provider=provider, model_id='jev-latest', system_prompt='Detection policy.',
        user_prompt=_prompt(), temperature=0.7, max_tokens=1, timeout=30,
        response_format='json_object', max_retries=1))


def test_native_retries_only_failed_chunk_and_keeps_every_request_usage(monkeypatch):
    payloads = []

    def handler(request):
        payload = json.loads(request.content)
        payloads.append(payload)
        assert set(payload) == {'state', 'model', 'questions'}
        assert request.url == httpx.URL('https://api.typesafe.ai/v1/systemone')
        if len(payloads) == 2:
            return httpx.Response(503, headers={'Retry-After': '0'}, json={
                'error': {'type': 'temporary'}, 'usage': {'input_tokens': 1000, 'output_tokens': 5}})
        return httpx.Response(200, json=_answer(payload))

    _http(monkeypatch, handler)
    response = _call(_provider())
    assert len(payloads) == 3
    assert payloads[0]['questions'] != payloads[1]['questions']
    assert payloads[1] == payloads[2]
    assert json.loads(response.text) == {'ads': []}
    assert response.input_tokens == 3000 and response.output_tokens == 15
    assert response.native_accounting['request_count'] == 3
    assert response.native_accounting['unknown_cost_request_count'] == 0
    assert Decimal(response.native_accounting['known_cost_usd']) == Decimal('0.000126')


def test_native_later_error_keeps_partial_usage_without_outer_replay(monkeypatch):
    payloads = []

    def handler(request):
        payload = json.loads(request.content)
        payloads.append(payload)
        return httpx.Response(200, json=_answer(payload)) if len(payloads) == 1 else httpx.Response(
            401, json={'error': {'type': 'authentication'}})

    _http(monkeypatch, handler)
    with pytest.raises(llm.LLMNonRetryableError) as caught:
        _call(_provider())
    assert len(payloads) == 2
    usage = caught.value.native_accounting
    assert usage['input_tokens'] == 1000 and usage['output_tokens'] == 5
    assert usage['unknown_usage_request_count'] == usage['unknown_cost_request_count'] == 1
    assert Decimal(usage['known_cost_usd']) == Decimal('0.000042')


def test_compatible_usage_and_cost_stay_unknown_without_reported_values(monkeypatch):
    _http(monkeypatch, lambda request: httpx.Response(
        200, json=_answer(json.loads(request.content), usage=False)))
    response = _call(_provider('systemone_compatible'))
    assert response.native_accounting['unknown_usage_request_count'] == 2
    assert response.native_accounting['unknown_cost_request_count'] == 2
    assert response.native_accounting['cost_source'] == 'unknown'


def test_native_deadline_does_not_turn_retry_wait_into_another_request(monkeypatch):
    calls = []
    clock = [0.0]
    monkeypatch.setattr(systemone.time, 'monotonic', lambda: clock[0])

    def handler(request):
        calls.append(request)
        clock[0] = 2.0
        return httpx.Response(503, json={'error': {'type': 'temporary'}})

    _http(monkeypatch, handler)
    with pytest.raises(llm.LLMNonRetryableError) as caught:
        _call(_provider(request_deadline_seconds=1.0))
    assert len(calls) == caught.value.native_accounting['request_count'] == 1


def test_async_cancellation_stops_following_chunks_and_keeps_paid_usage(monkeypatch):
    started, release = threading.Event(), threading.Event()
    requests = []

    def handler(request):
        requests.append(request)
        started.set()
        assert release.wait(5)
        return httpx.Response(200, json=_answer(json.loads(request.content)))

    _http(monkeypatch, handler)

    async def cancel():
        task = asyncio.create_task(llm.call(
            provider=_provider(), model_id='jev-latest', system_prompt='Detection policy.',
            user_prompt=_prompt(), temperature=0, max_tokens=1, timeout=30))
        assert await asyncio.to_thread(started.wait, 5)
        task.cancel()
        await asyncio.sleep(0)
        release.set()
        with pytest.raises(asyncio.CancelledError) as caught:
            await task
        assert caught.value.native_accounting['input_tokens'] == 1000

    asyncio.run(cancel())
    assert len(requests) == 1


def test_native_configuration_keeps_profiles_independent(tmp_path):
    cfg = config._parse({
        'minuspod': {'base_url': 'https://example.com', 'password_env': 'PASSWORD'},
        'providers': {
            'a': {'client': 'typesafe', 'api_key_env': 'TYPESAFE_API_KEY',
                  'systemone': {'detectionEnter': 0.98}},
            'b': {'client': 'systemone_compatible', 'base_url': 'https://example.com/v1',
                  'systemone': {'detectionEnter': 0.90}},
        },
        'models': [{'id': 'jev-latest', 'provider': 'a'}],
    }, tmp_path)
    assert cfg.providers['a'].systemone['detection_enter'] == 0.98
    assert cfg.providers['b'].systemone['detection_enter'] == 0.90
    assert cfg.providers['b'].api_key_env is None


def test_native_runner_persists_partial_error_accounting(
        tmp_path, minimal_cfg, make_episode, pricing_snapshot, monkeypatch):
    native_usage = {
        'request_count': 2, 'input_tokens': 1000, 'output_tokens': 5,
        'known_cost_usd': '0.000042', 'unknown_cost_request_count': 1,
        'unknown_usage_request_count': 1, 'cost_source': 'estimated',
    }

    async def failure(**kwargs):
        error = llm.LLMNonRetryableError('System One AuthenticationError')
        error.native_accounting = native_usage
        raise error

    monkeypatch.setattr(runner.llm, 'call_with_retry', failure)
    paths = runner.RunPaths.for_root(tmp_path)
    stats = asyncio.run(runner.run(minimal_cfg, [make_episode(n_windows=1)], paths=paths,
                                   pricing_snapshot=pricing_snapshot, system_prompt='Policy.'))
    rows = list(read_jsonl(paths.calls_jsonl))
    assert stats.errored == 2
    assert all(row['input_tokens'] == 1000 for row in rows)
    assert all(row['known_total_cost_usd_at_runtime'] == '0.000042' for row in rows)
    assert all(row['total_cost_usd_at_runtime'] is None for row in rows)


def test_benchmark_admission_timeout_retains_zero_requests(monkeypatch):
    deadline_at = systemone.time.monotonic() + 2
    with systemone.operation_admission('https://api.typesafe.ai/v1/systemone', None, 1,
                                      deadline_at=deadline_at):
        with pytest.raises(systemone.NativeCallError) as raised:
            systemone.call_native(provider=_provider(request_deadline_seconds=0.02),
                                  model_id='jev-latest', system_prompt='policy', user_prompt=_prompt(),
                                  timeout=1, max_retries=2, cancel_event=threading.Event())
    assert raised.value.native_accounting['request_count'] == 0
    assert raised.value.native_accounting['known_cost_usd'] == '0'
    assert raised.value.native_accounting['unknown_cost_request_count'] == 0


def test_native_benchmark_retains_existing_seeded_sponsor_fixture(monkeypatch):
    fixture_path = Path(__file__).resolve().parents[3] / 'tests/unit/test_systemone_adapter_detection.py'
    tree = ast.parse(fixture_path.read_text())
    fixture = next(node.value for node in tree.body if isinstance(node, ast.Assign)
                   and any(isinstance(target, ast.Name) and target.id == 'TS_LINES' for target in node.targets))
    lines = ast.literal_eval(fixture)
    user_prompt = 'Podcast: Example\nEpisode title: Example\nTranscript:\n' + '\n'.join(lines)

    def handler(request):
        payload = json.loads(request.content)
        return httpx.Response(200, json={
            'answers': {key: {'noul': 0.99 if key in {'s3', 's4', 's5'} else 0.01}
                        for key in payload['questions']},
            'usage': {'input_tokens': 1000, 'output_tokens': 5}})

    _http(monkeypatch, handler)
    content, accounting = systemone.call_native(
        provider=_provider(max_questions_per_request=None), model_id='jev-latest',
        system_prompt='Detection policy.', user_prompt=user_prompt,
        timeout=1, max_retries=0, cancel_event=threading.Event())
    ads = json.loads(content)['ads']
    assert len(ads) == 1 and ads[0]['sponsor_name'] == 'BetterHelp'
    assert accounting['request_count'] == 1


def test_benchmark_expired_dispatch_callback_does_not_count_a_post(monkeypatch):
    _http(monkeypatch, lambda _request: (_ for _ in ()).throw(AssertionError('expired callback cannot POST')))
    original = systemone.SystemOneTransport.probe_once

    def slow_callback(self, payload, **kwargs):
        dispatched = kwargs['on_dispatch']
        def delayed():
            dispatched()
            systemone.time.sleep(0.03)
        kwargs['on_dispatch'] = delayed
        return original(self, payload, **kwargs)

    monkeypatch.setattr(systemone.SystemOneTransport, 'probe_once', slow_callback)
    with pytest.raises(systemone.NativeCallError) as raised:
        systemone.call_native(provider=_provider(request_deadline_seconds=0.02),
                              model_id='jev-latest', system_prompt='Detection policy.', user_prompt=_prompt(),
                              timeout=1, max_retries=0, cancel_event=threading.Event())
    assert raised.value.native_accounting['request_count'] == 0
    assert raised.value.native_accounting['unknown_usage_request_count'] == 0
    assert raised.value.native_accounting['unknown_cost_request_count'] == 0
