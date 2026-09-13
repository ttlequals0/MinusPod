"""Regression: provider budget reconcile must see collected totals, not zeros.

get_episode_token_totals() is one-shot and destructive, so the history path
consumes the accumulator first and a second destructive read reconciles 0.
The budget path must use the non-destructive get_last_episode_token_totals().
"""

import threading

import run_context
from llm_client import (
    get_episode_token_totals,
    get_last_episode_token_totals,
)


def test_last_totals_survives_collect_and_reset():
    ctx = run_context.begin('feed', 'ep1')
    try:
        ctx.tokens.start()
        ctx.tokens.add(166117, 596, 0.068906)
        first = ctx.tokens.collect_and_reset()
        assert first == {
            'input_tokens': 166117, 'output_tokens': 596, 'cost': 0.068906}
        # Late reader still sees the real totals after the reset.
        assert ctx.tokens.last_totals() == first
        assert ctx.tokens.last_totals()['cost'] != 0.0
        second = ctx.tokens.collect_and_reset()
        assert second == {'input_tokens': 0, 'output_tokens': 0, 'cost': 0.0}
    finally:
        run_context.end(ctx)


def test_last_totals_returns_copy():
    ctx = run_context.begin('feed', 'ep1')
    try:
        ctx.tokens.start()
        ctx.tokens.add(10, 5, 0.5)
        ctx.tokens.collect_and_reset()
        snapshot = ctx.tokens.last_totals()
        snapshot['cost'] = 999.0
        assert ctx.tokens.last_totals()['cost'] == 0.5
    finally:
        run_context.end(ctx)


def test_budget_reconcile_sees_history_totals():
    ctx = run_context.begin('feed', 'ep1')
    try:
        ctx.tokens.start()
        ctx.tokens.add(166117, 596, 0.068906)
        # History path consumes the accumulator first (one-shot).
        history = get_episode_token_totals()
        assert history['cost'] == 0.068906
        # Budget reconcile reads the same number non-destructively.
        late = get_last_episode_token_totals()
        assert late == history
        assert late['cost'] == 0.068906
    finally:
        run_context.end(ctx)


def test_get_last_totals_without_context_returns_zeros():
    results = {}

    def fresh_thread():
        results['totals'] = get_last_episode_token_totals()

    t = threading.Thread(target=fresh_thread)
    t.start()
    t.join()
    assert results['totals'] == {
        'input_tokens': 0, 'output_tokens': 0, 'cost': 0.0}
