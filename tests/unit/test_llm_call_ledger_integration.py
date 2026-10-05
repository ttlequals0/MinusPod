"""Integration tests: every utils.llm_call dispatch is one ledger attempt.

Covers the contract that begin_llm_attempt/finalize_llm_attempt
are the single writer of billed LLM calls.
"""
from unittest.mock import patch

import run_context
from llm_route import Route
from llm_client import LLMResponse, ProviderRateLimitedError
from utils.llm_call import EmptyCompletionError, call_llm
from utils import llm_call


class _FakeLLMClient:
    """Returns/raises each entry of ``results`` in sequence per messages_create call."""

    def __init__(self, results):
        self._results = list(results)
        self.calls = 0

    def messages_create(self, **kwargs):
        self.calls += 1
        item = self._results.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class _InconclusiveError(Exception):
    status_code = 422
    body = {'error': {'code': 'jev_review_inconclusive'}}

    def __init__(self, response=None):
        super().__init__('abstained')
        self.response = response


class _Other422Error(Exception):
    status_code = 422
    body = {'error': {'code': 'other_provider_error'}}


def _rows_for_episode(db, episode_id):
    return db.get_connection().execute(
        "SELECT * FROM llm_call_usage WHERE episode_id = ? ORDER BY created_at",
        (episode_id,)
    ).fetchall()


def test_success_call_creates_one_success_ledger_row(temp_db, monkeypatch):
    monkeypatch.setattr('utils.llm_call.time.sleep', lambda s: None)
    temp_db.create_podcast('show-a', 'https://example.com/a.xml', 'Show A')
    ctx = run_context.begin('show-a', 'ep-success', run_id='run-success')
    try:
        client = _FakeLLMClient([
            LLMResponse(content='ok', model='claude-x',
                       usage={'input_tokens': 10, 'output_tokens': 5},
                       returned_model='claude-x-v2'),
        ])
        response, error = call_llm(
            llm_client=client, model='claude-x', system_prompt='s', prompt='p',
            llm_timeout=30, max_retries=0, max_tokens=100,
            slug='show-a', episode_id='ep-success', call_label='window 1',
            phase_key='detection', pass_name='ad_detection_pass_1',
            provider='anthropic',
        )
        assert error is None
        assert response.content == 'ok'

        rows = _rows_for_episode(temp_db, 'ep-success')
        assert len(rows) == 1
        row = rows[0]
        assert row['state'] == 'success'
        assert row['phase_key'] == 'detection'
        assert row['run_id'] == 'run-success'
        assert row['provider_key'] == 'anthropic'
        assert row['invoking_pass'] == 1
        assert row['returned_model'] == 'claude-x-v2'
        assert row['input_tokens'] == 10
        assert row['output_tokens'] == 5
    finally:
        run_context.end(ctx)


def test_failure_after_billable_partial_creates_one_failure_row(temp_db):
    temp_db.create_podcast('show-b', 'https://example.com/b.xml', 'Show B')
    ctx = run_context.begin('show-b', 'ep-failure', run_id='run-failure')
    try:
        client = _FakeLLMClient([_Other422Error('non-retryable provider error')])
        response, error = call_llm(
            llm_client=client, model='claude-x', system_prompt='s', prompt='p',
            llm_timeout=30, max_retries=0, max_tokens=100,
            slug='show-b', episode_id='ep-failure', call_label='window 1',
            phase_key='detection', pass_name='ad_detection_pass_1',
            provider='anthropic',
        )
        assert response is None
        assert isinstance(error, _Other422Error)

        rows = _rows_for_episode(temp_db, 'ep-failure')
        assert len(rows) == 1
        assert rows[0]['state'] == 'failure'
        assert client.calls == 1
    finally:
        run_context.end(ctx)


def test_inconclusive_call_creates_inconclusive_ledger_row(temp_db):
    temp_db.create_podcast('show-inconclusive', 'https://example.com/i.xml', 'Show I')
    ctx = run_context.begin('show-inconclusive', 'ep-inconclusive', run_id='run-inconclusive')
    try:
        billed = LLMResponse(
            content='', model='claude-x',
            usage={'input_tokens': 10, 'output_tokens': 5},
            provider_reported_cost_usd=0.42,
        )
        response, error = call_llm(
            llm_client=_FakeLLMClient([_InconclusiveError(billed)]),
            model='claude-x', system_prompt='s', prompt='p', llm_timeout=30,
            max_retries=2, max_tokens=100, slug='show-inconclusive',
            episode_id='ep-inconclusive', call_label='review window 1',
            phase_key='review', pass_name='ad_review_pass_1', provider='anthropic',
        )

        assert response is None
        assert isinstance(error, _InconclusiveError)
        rows = _rows_for_episode(temp_db, 'ep-inconclusive')
        assert len(rows) == 1
        assert rows[0]['state'] == 'inconclusive'
        assert rows[0]['finalized_at'] is not None
        totals = temp_db.get_run_usage_totals('run-inconclusive')
        assert totals['input_tokens'] == 10
        assert totals['output_tokens'] == 5
        assert totals['cost_usd'] == '0.42'
        assert temp_db.get_run_provider_spend(
            'run-inconclusive', 'anthropic') == 420_000
    finally:
        run_context.end(ctx)


def test_empty_completion_records_the_billed_usage_on_the_failure_row(temp_db, monkeypatch):
    """A content-less completion still bills tokens; the failed attempt must
    record them, not zero."""
    monkeypatch.setattr('utils.llm_call.time.sleep', lambda s: None)
    temp_db.create_podcast('show-e', 'https://example.com/e.xml', 'Show E')
    ctx = run_context.begin('show-e', 'ep-empty', run_id='run-empty')
    try:
        empty = [LLMResponse(content='', model='claude-x',
                             usage={'input_tokens': 7, 'output_tokens': 0})
                 for _ in range(3)]
        response, error = call_llm(
            llm_client=_FakeLLMClient(empty), model='claude-x', system_prompt='s',
            prompt='p', llm_timeout=30, max_retries=0, max_tokens=100,
            slug='show-e', episode_id='ep-empty', call_label='window 1',
            phase_key='detection', provider='anthropic',
        )
        assert response is None
        assert isinstance(error, EmptyCompletionError)

        rows = _rows_for_episode(temp_db, 'ep-empty')
        assert rows
        assert all(r['state'] == 'failure' for r in rows)
        assert all(r['input_tokens'] == 7 for r in rows)
        totals = temp_db.get_run_usage_totals('run-empty')
        assert totals['input_tokens'] == 7 * len(rows)
    finally:
        run_context.end(ctx)


def test_manual_cap_crossed_mid_retry_defers(temp_db, monkeypatch):
    """A per-minute cap reached after the first dispatch must defer the retry
    with a manual hold, not keep spending requests (#747)."""
    monkeypatch.setattr('utils.llm_call.time.sleep', lambda s: None)
    temp_db.set_setting('provider_requests_per_min', '1')
    temp_db.create_podcast('show-cap', 'https://example.com/cap.xml', 'Show Cap')
    ctx = run_context.begin('show-cap', 'ep-cap', run_id='run-cap')
    try:
        client = _FakeLLMClient([
            EmptyCompletionError('empty, retryable'),
            LLMResponse(content='ok', model='claude-x',
                        usage={'input_tokens': 1, 'output_tokens': 1}),
        ])
        response, error = call_llm(
            llm_client=client, model='claude-x', system_prompt='s', prompt='p',
            llm_timeout=30, max_retries=1, max_tokens=100,
            slug='show-cap', episode_id='ep-cap', call_label='window 1',
            phase_key='detection', provider='anthropic',
        )
        assert response is None
        assert isinstance(error, ProviderRateLimitedError)
        assert error.manual is True
        # Only the first dispatch happened; the cap deferred the retry.
        assert client.calls == 1
    finally:
        run_context.end(ctx)


def test_retry_that_reissues_creates_two_rows_with_distinct_attempt_ids(temp_db, monkeypatch):
    monkeypatch.setattr('utils.llm_call.time.sleep', lambda s: None)
    temp_db.create_podcast('show-c', 'https://example.com/c.xml', 'Show C')
    ctx = run_context.begin('show-c', 'ep-retry', run_id='run-retry')
    try:
        client = _FakeLLMClient([
            EmptyCompletionError('empty completion, retryable'),
            LLMResponse(content='ok', model='claude-x',
                       usage={'input_tokens': 20, 'output_tokens': 8},
                       returned_model='claude-x-v2'),
        ])
        response, error = call_llm(
            llm_client=client, model='claude-x', system_prompt='s', prompt='p',
            llm_timeout=30, max_retries=1, max_tokens=100,
            slug='show-c', episode_id='ep-retry', call_label='window 1',
            phase_key='detection', pass_name='ad_detection_pass_1',
            provider='anthropic',
        )
        assert error is None
        assert response.content == 'ok'
        assert client.calls == 2

        rows = _rows_for_episode(temp_db, 'ep-retry')
        assert len(rows) == 2
        attempt_ids = {row['attempt_id'] for row in rows}
        assert len(attempt_ids) == 2
        states = sorted(row['state'] for row in rows)
        assert states == ['failure', 'success']
    finally:
        run_context.end(ctx)


def test_counters_increment_once_not_twice_via_ledger_alone(temp_db):
    """Counters come from the ledger alone: one successful call increments
    token_usage by exactly its own tokens, not double."""
    temp_db.create_podcast('show-d', 'https://example.com/d.xml', 'Show D')
    ctx = run_context.begin('show-d', 'ep-once', run_id='run-once')
    try:
        client = _FakeLLMClient([
            LLMResponse(content='ok', model='claude-x',
                       usage={'input_tokens': 100, 'output_tokens': 50},
                       returned_model='claude-x-v2'),
        ])
        response, error = call_llm(
            llm_client=client, model='claude-x', system_prompt='s', prompt='p',
            llm_timeout=30, max_retries=0, max_tokens=100,
            slug='show-d', episode_id='ep-once', call_label='window 1',
            phase_key='detection', pass_name='ad_detection_pass_1',
            provider='anthropic',
        )
        assert error is None

        summary = temp_db.get_token_usage_summary()
        assert summary['totalInputTokens'] == 100
        assert summary['totalOutputTokens'] == 50
    finally:
        run_context.end(ctx)


def test_secondary_slot_fallback_retry_tags_ledger_secondary(temp_db, monkeypatch):
    """The per-window fallback retry must carry credential_slot, so a
    secondary-slot call's retries are not mis-tagged primary (#747)."""
    monkeypatch.setattr('utils.llm_call.time.sleep', lambda s: None)
    temp_db.create_podcast('show-slot', 'https://example.com/slot.xml', 'Show Slot')
    ctx = run_context.begin('show-slot', 'ep-slot', run_id='run-slot')
    try:
        client = _FakeLLMClient([
            EmptyCompletionError('empty, retryable'),
            LLMResponse(content='ok', model='claude-x',
                        usage={'input_tokens': 3, 'output_tokens': 2}),
        ])
        response, error = call_llm(
            llm_client=client, model='claude-x', system_prompt='s', prompt='p',
            llm_timeout=30, max_retries=0, max_tokens=100,
            slug='show-slot', episode_id='ep-slot', call_label='window 1',
            phase_key='detection', provider='anthropic', credential_slot='secondary',
        )
        assert error is None
        rows = _rows_for_episode(temp_db, 'ep-slot')
        assert len(rows) == 2
        assert [r['credential_slot'] for r in rows] == ['secondary', 'secondary']
    finally:
        run_context.end(ctx)


def test_actual_standby_dispatch_retains_both_origins_after_failure(temp_db):
    temp_db.create_podcast('standby-usage', 'https://example.com/feed.xml', 'Route Test')
    ctx = run_context.begin('standby-usage', 'episode', run_id='usage-run')
    ctx.set_route_snapshot({
        'detection': {'credential_slot': 'primary'},
        'verification': {'credential_slot': 'secondary'},
        'review': {'gate': {'review_provider': 'same_as_pass'}}})
    try:
        for phase, pass_name, outcome in (
                ('detection', 'ad_detection_pass_1', LLMResponse(content='ok', model='standby')),
                ('review', 'ad_review_pass_2', _Other422Error('standby failed'))):
            call_llm(
                llm_client=_FakeLLMClient([outcome]), model='standby', system_prompt='s', prompt='p',
                llm_timeout=1, max_retries=0, max_tokens=10, slug='standby-usage', episode_id='episode',
                call_label=phase, phase_key=phase, pass_name=pass_name,
                provider='openai-compatible', credential_slot='failover')
        assert ctx.failover_usage() == {'llm': ['primary', 'secondary'], 'whisper': False}
        assert len(_rows_for_episode(temp_db, 'episode')) == 2
    finally:
        run_context.end(ctx)


def test_refused_standby_reservation_records_no_usage(temp_db, monkeypatch):
    ctx = run_context.begin('standby-usage', 'refused', run_id='refused-run')
    ctx.set_route_snapshot({'detection': {'credential_slot': 'primary'}})
    monkeypatch.setattr(llm_call, 'reserve_provider_request', lambda *a, **k: (None, None))
    monkeypatch.setattr(llm_call, '_reservation_refused',
                        lambda *a: ProviderRateLimitedError('manual cap', 10, manual=True))
    client = _FakeLLMClient([])
    try:
        try:
            llm_call._ledger_call_once(
                client, {}, 'standby', phase_key='detection', invoking_pass=1,
                provider_key='openai-compatible', credential_slot='failover',
                slug='standby-usage', episode_id='refused', call_label='window')
        except ProviderRateLimitedError:
            pass
        else:
            raise AssertionError('reservation should fail')
        assert ctx.failover_usage() is None
        assert client.calls == 0
    finally:
        run_context.end(ctx)


def test_standby_dispatch_without_snapshot_records_explicit_original_slot(temp_db):
    temp_db.create_podcast('unresolved-route', 'https://example.com/feed.xml', 'Route Test')
    ctx = run_context.begin('unresolved-route', 'episode', run_id='unresolved-route')
    try:
        primary = _FakeLLMClient([ConnectionError('primary unavailable')])
        standby = _FakeLLMClient([LLMResponse(content='ok', model='standby')])
        route = Route(phase='detection', provider_key='openai-compatible', model_id='standby',
                      base_url='http://example.com/v1', slot='failover', credential_slot='failover')
        with patch.object(llm_call, '_manual_rate_limit_error', return_value=None), \
                patch.object(llm_call, '_sleep_before_retry', return_value=False), \
                patch.object(llm_call.failover, 'is_configured', return_value=True), \
                patch.object(llm_call.failover, 'trigger', return_value=True), \
                patch.object(llm_call, '_failover_route', return_value=route), \
                patch.object(llm_call, 'client_for_route', return_value=standby):
            response, error = call_llm(
                llm_client=primary, model='primary', system_prompt='s', prompt='p',
                llm_timeout=1, max_retries=0, max_tokens=10, slug='unresolved-route', episode_id='episode',
                call_label='window', phase_key='detection', provider='openai-compatible', credential_slot='secondary')
        assert error is None and response.content == 'ok'
        assert standby.calls == 1
        assert ctx.failover_usage() == {'llm': ['secondary'], 'whisper': False}
    finally:
        run_context.end(ctx)
