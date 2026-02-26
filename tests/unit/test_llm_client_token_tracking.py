"""Tests for per-episode token accumulator thread safety."""

import threading

from llm_client import (
    start_episode_token_tracking,
    get_episode_token_totals,
    _get_accumulator_active,
    _record_token_usage,
)


def test_thread_local_isolation():
    """Two threads accumulate different values concurrently; each gets its own totals."""
    barrier = threading.Barrier(2)
    results = {}

    def accumulate(thread_id, input_tok, output_tok):
        start_episode_token_tracking()
        assert _get_accumulator_active()
        barrier.wait()  # Force both threads to overlap
        _record_token_usage(
            "claude-test",
            {"input_tokens": input_tok, "output_tokens": output_tok},
        )
        totals = get_episode_token_totals()
        results[thread_id] = totals

    t1 = threading.Thread(target=accumulate, args=("t1", 100, 50))
    t2 = threading.Thread(target=accumulate, args=("t2", 200, 75))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    # Each thread should see only its own tokens (cost may vary by pricing table
    # availability, so we only assert on token counts)
    assert results["t1"]["input_tokens"] == 100
    assert results["t1"]["output_tokens"] == 50
    assert results["t2"]["input_tokens"] == 200
    assert results["t2"]["output_tokens"] == 75


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
