"""Integration tests: every utils.llm_call dispatch is one ledger attempt.

Covers the contract that begin_llm_attempt/finalize_llm_attempt
are the single writer of billed LLM calls, replacing the retired adapter
usage callback (llm_client._record_token_usage).
"""
import run_context
from llm_client import LLMResponse
from utils.llm_call import EmptyCompletionError, call_llm


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
        client = _FakeLLMClient([ValueError('non-retryable provider error')])
        response, error = call_llm(
            llm_client=client, model='claude-x', system_prompt='s', prompt='p',
            llm_timeout=30, max_retries=0, max_tokens=100,
            slug='show-b', episode_id='ep-failure', call_label='window 1',
            phase_key='detection', pass_name='ad_detection_pass_1',
            provider='anthropic',
        )
        assert response is None
        assert isinstance(error, ValueError)

        rows = _rows_for_episode(temp_db, 'ep-failure')
        assert len(rows) == 1
        assert rows[0]['state'] == 'failure'
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
    """The retired record_token_usage callback path must not also write
    counters: one successful call increments token_usage by exactly its
    own tokens, not double."""
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
