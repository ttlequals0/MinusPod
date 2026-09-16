"""Tests for the per-run token accumulator.

Totals are keyed by run_context, per thread, and pool workers bind to
their submitter's run via run_context.run_in_worker_thread, so several
episodes can run concurrently in one process without sharing totals.
"""

import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock

import run_context
from llm_client import (
    start_episode_token_tracking,
    get_episode_token_totals,
    AnthropicClient,
    OpenAICompatibleClient,
)


def test_parallel_workers_aggregate_into_shared_accumulator():
    """Pool workers bound to the same run both get their contributions
    counted, with no torn increments. This is the behavior ad-detection
    windows depend on."""
    ctx = run_context.begin('feed', 'ep')
    try:
        start_episode_token_tracking()
        barrier = threading.Barrier(2)

        def accumulate(input_tok, output_tok):
            barrier.wait()  # Force both threads to overlap
            run_context.current().tokens.add(input_tok, output_tok, 0.0)

        with ThreadPoolExecutor(max_workers=2) as exe:
            f1 = exe.submit(run_context.run_in_worker_thread(accumulate), 100, 50)
            f2 = exe.submit(run_context.run_in_worker_thread(accumulate), 200, 75)
            f1.result()
            f2.result()

        totals = get_episode_token_totals()
        assert totals["input_tokens"] == 300
        assert totals["output_tokens"] == 125
    finally:
        run_context.end(ctx)


def test_high_concurrency_no_lost_updates():
    """Hammer the accumulator from 16 bound worker threads and verify the
    total exactly matches the expected sum (no double-counting, no lost
    updates)."""
    ctx = run_context.begin('feed', 'ep')
    try:
        start_episode_token_tracking()
        n_threads = 16
        calls_per_thread = 50
        per_call = 10

        def worker():
            for _ in range(calls_per_thread):
                run_context.current().tokens.add(per_call, per_call, 0.0)

        with ThreadPoolExecutor(max_workers=n_threads) as exe:
            futures = [exe.submit(run_context.run_in_worker_thread(worker))
                       for _ in range(n_threads)]
            for f in futures:
                f.result()

        totals = get_episode_token_totals()
        expected = n_threads * calls_per_thread * per_call
        assert totals["input_tokens"] == expected
        assert totals["output_tokens"] == expected
    finally:
        run_context.end(ctx)


def test_get_totals_without_start_returns_zeros():
    """A fresh thread with no run context gets zero totals."""
    results = {}

    def fresh_thread():
        assert run_context.current() is None
        results["totals"] = get_episode_token_totals()

    t = threading.Thread(target=fresh_thread)
    t.start()
    t.join()

    assert results["totals"]["input_tokens"] == 0
    assert results["totals"]["output_tokens"] == 0
    assert results["totals"]["cost"] == 0.0


def test_accumulator_resets_after_get_totals():
    """After retrieving totals, the run's accumulator is deactivated and zeroed."""
    ctx = run_context.begin('feed', 'ep')
    try:
        start_episode_token_tracking()
        run_context.current().tokens.add(500, 250, 0.0)
        first = get_episode_token_totals()

        assert first["input_tokens"] == 500
        assert first["output_tokens"] == 250
        assert not run_context.current().tokens.is_active()

        second = get_episode_token_totals()
        assert second["input_tokens"] == 0
        assert second["output_tokens"] == 0
        assert second["cost"] == 0.0
    finally:
        run_context.end(ctx)


def test_two_runs_on_two_threads_do_not_share_totals():
    """Two runs on two different threads accumulate independently."""
    seen = {}

    def run(name, input_tok):
        ctx = run_context.begin('feed', name)
        try:
            start_episode_token_tracking()
            run_context.current().tokens.add(input_tok, 1, 0.0)
            seen[name] = get_episode_token_totals()
        finally:
            run_context.end(ctx)

    t1 = threading.Thread(target=run, args=('a', 10))
    t2 = threading.Thread(target=run, args=('b', 20))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert seen['a']['input_tokens'] == 10
    assert seen['b']['input_tokens'] == 20


def _anthropic_client_with_mock(usage_kwargs, model="claude-response-model"):
    client = AnthropicClient(api_key="test-key")
    client._client = MagicMock()
    text_block = MagicMock(type="text", text="hi")
    response = MagicMock()
    response.content = [text_block]
    response.stop_reason = "end_turn"
    response.model = model
    response.usage = MagicMock(input_tokens=10, output_tokens=5, **usage_kwargs)
    client._client.messages.create.return_value = response
    return client


def test_anthropic_usage_carries_cache_tokens_when_present():
    """Provider-reported cache tokens land in usage under their own keys."""
    client = _anthropic_client_with_mock(
        {"cache_creation_input_tokens": 30, "cache_read_input_tokens": 70}
    )
    result = client.messages_create(
        model="claude-test", max_tokens=100, system="s",
        messages=[{"role": "user", "content": "hi"}],
    )
    assert result.usage["cache_write_tokens"] == 30
    assert result.usage["cache_read_tokens"] == 70
    assert result.usage["input_tokens"] == 10
    assert result.usage["output_tokens"] == 5
    assert result.returned_model == "claude-response-model"


def test_anthropic_usage_omits_cache_keys_when_absent():
    """No cache fields on the provider response means no keys, not zeros."""
    client = _anthropic_client_with_mock(
        {"cache_creation_input_tokens": None, "cache_read_input_tokens": None}
    )
    result = client.messages_create(
        model="claude-test", max_tokens=100, system="s",
        messages=[{"role": "user", "content": "hi"}],
    )
    assert "cache_write_tokens" not in result.usage
    assert "cache_read_tokens" not in result.usage


def _openai_client_with_mock(completion_tokens_details=None, prompt_tokens_details=None,
                              model="gpt-response-model"):
    client = OpenAICompatibleClient(
        base_url="http://localhost:8000/v1", api_key="test-key", default_model="test-model"
    )
    client._client = MagicMock()
    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message.content = "hi"
    response.choices[0].message.reasoning = None
    response.choices[0].message.reasoning_details = None
    response.choices[0].finish_reason = "stop"
    response.model = model
    response.usage = MagicMock(
        prompt_tokens=10, completion_tokens=5,
        completion_tokens_details=completion_tokens_details,
        prompt_tokens_details=prompt_tokens_details,
    )
    client._client.chat.completions.create.return_value = response
    return client


def test_openai_usage_carries_reasoning_and_cache_tokens_when_present():
    """Provider-reported reasoning/cache tokens land in usage under their own keys."""
    client = _openai_client_with_mock(
        completion_tokens_details=MagicMock(reasoning_tokens=15),
        prompt_tokens_details=MagicMock(cached_tokens=8),
    )
    result = client.messages_create(
        model="test-model", max_tokens=100, system="s",
        messages=[{"role": "user", "content": "hi"}],
    )
    assert result.usage["reasoning_tokens"] == 15
    assert result.usage["cache_read_tokens"] == 8
    assert result.returned_model == "gpt-response-model"


def test_openai_usage_omits_reasoning_and_cache_keys_when_absent():
    """No details objects on the provider response means no keys, not zeros."""
    client = _openai_client_with_mock(
        completion_tokens_details=None, prompt_tokens_details=None,
    )
    result = client.messages_create(
        model="test-model", max_tokens=100, system="s",
        messages=[{"role": "user", "content": "hi"}],
    )
    assert "reasoning_tokens" not in result.usage
    assert "cache_read_tokens" not in result.usage
