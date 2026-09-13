"""Provider-budget reconciliation retains collected token totals."""

import threading

import run_context
from llm_client import (
    get_episode_token_totals,
    get_last_episode_token_totals,
)


def test_last_totals_survives_inactive_collection():
    ctx = run_context.begin('feed', 'ep1')
    try:
        ctx.tokens.start()
        ctx.tokens.add(120, 30, 0.012)
        first = ctx.tokens.collect_and_reset()
        assert first == {
            'input_tokens': 120, 'output_tokens': 30, 'cost': 0.012}
        assert ctx.tokens.last_totals() == first
        second = ctx.tokens.collect_and_reset()
        assert second == {'input_tokens': 0, 'output_tokens': 0, 'cost': 0.0}
        assert ctx.tokens.last_totals() == first
    finally:
        run_context.end(ctx)


def test_start_clears_previous_run_snapshot():
    ctx = run_context.begin('feed', 'ep1')
    try:
        ctx.tokens.start()
        ctx.tokens.add(120, 30, 0.012)
        ctx.tokens.collect_and_reset()
        ctx.tokens.start()
        assert ctx.tokens.last_totals() == {
            'input_tokens': 0, 'output_tokens': 0, 'cost': 0.0}
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
        ctx.tokens.add(120, 30, 0.012)
        history = get_episode_token_totals()
        assert history['cost'] == 0.012
        late = get_last_episode_token_totals()
        assert late == history
        assert late['cost'] == 0.012
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
