"""Tests for the per-run token accumulator.

Totals are keyed by run_context, per thread, and pool workers bind to
their submitter's run via run_context.run_in_worker_thread, so several
episodes can run concurrently in one process without sharing totals.
"""

import threading
from concurrent.futures import ThreadPoolExecutor

import run_context
from llm_client import (
    start_episode_token_tracking,
    get_episode_token_totals,
    _get_accumulator_active,
    _record_token_usage,
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
            _record_token_usage(
                "claude-test",
                {"input_tokens": input_tok, "output_tokens": output_tok},
            )

        with ThreadPoolExecutor(max_workers=2) as exe:
            f1 = exe.submit(run_context.run_in_worker_thread(accumulate), 100, 50)
            f2 = exe.submit(run_context.run_in_worker_thread(accumulate), 200, 75)
            f1.result()
            f2.result()

        totals = get_episode_token_totals()
        # Cost varies by pricing table availability; only assert tokens.
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
        assert not _get_accumulator_active()
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
        _record_token_usage("claude-test", {"input_tokens": 500, "output_tokens": 250})
        first = get_episode_token_totals()

        assert first["input_tokens"] == 500
        assert first["output_tokens"] == 250
        assert not _get_accumulator_active()

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
            _record_token_usage("claude-test", {"input_tokens": input_tok, "output_tokens": 1})
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
