"""Tests for the per-episode token accumulator.

Pre-2.5.23 the accumulator was thread-local so concurrent episode handlers
couldn't corrupt each other. 2.5.23 replaced it with a single
lock-protected object so that ad-detection windows running on a
ThreadPoolExecutor all contribute to the same totals. Single-episode
isolation is now enforced upstream by the fcntl flock on
.processing_queue.lock, not by the accumulator. The tests below reflect
that contract change.
"""

import threading

from llm_client import (
    start_episode_token_tracking,
    get_episode_token_totals,
    _get_accumulator_active,
    _record_token_usage,
    _episode_accumulator,
)


def test_parallel_workers_aggregate_into_shared_accumulator():
    """Two threads making token-usage calls concurrently against the same
    active accumulator both get their contributions counted, with no torn
    increments. This is the behavior ad-detection windows depend on."""
    start_episode_token_tracking()
    barrier = threading.Barrier(2)

    def accumulate(input_tok, output_tok):
        barrier.wait()  # Force both threads to overlap
        _record_token_usage(
            "claude-test",
            {"input_tokens": input_tok, "output_tokens": output_tok},
        )

    t1 = threading.Thread(target=accumulate, args=(100, 50))
    t2 = threading.Thread(target=accumulate, args=(200, 75))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    totals = get_episode_token_totals()
    # Cost varies by pricing table availability; only assert tokens.
    assert totals["input_tokens"] == 300
    assert totals["output_tokens"] == 125


def test_high_concurrency_no_lost_updates():
    """Hammer the accumulator from 16 threads and verify the total exactly
    matches the expected sum (no double-counting, no lost updates)."""
    start_episode_token_tracking()
    n_threads = 16
    calls_per_thread = 50
    per_call = 10

    def worker():
        for _ in range(calls_per_thread):
            _episode_accumulator.add(per_call, per_call, 0.0)

    threads = [threading.Thread(target=worker) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    totals = get_episode_token_totals()
    expected = n_threads * calls_per_thread * per_call
    assert totals["input_tokens"] == expected
    assert totals["output_tokens"] == expected


def test_get_totals_without_start_returns_zeros():
    """A fresh thread that never started tracking gets zero totals."""
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
    """After retrieving totals, the accumulator is deactivated and zeroed."""
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
