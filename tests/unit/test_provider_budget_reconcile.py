"""Provider-budget reconciliation retains collected token totals."""

import threading

import run_context
from config import normalize_model_key
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


def _enable_budget(db, *, limit=1_000_000, concurrency=1):
    db.set_setting('provider_budget_enabled', 'true')
    db.set_setting('provider_budget_daily_limit_microusd', str(limit))
    db.set_setting('provider_budget_max_reservations', str(concurrency))


class TestProviderReconcileReadsTheLedger:
    """Provider reconcile derives actual_microusd from the same ledger
    sum as get_run_usage_totals, one source instead of a second
    independent re-derivation."""

    def test_reconcile_reflects_ledger_derived_spend(self, temp_db):
        temp_db.upsert_fetched_pricing([{
            'match_key': normalize_model_key('test-recon-model'),
            'raw_model_id': 'test-recon-model',
            'display_name': 'test-recon-model',
            'input_cost_per_mtok': 2.0,
            'output_cost_per_mtok': 4.0,
        }], source='litellm')
        _enable_budget(temp_db)
        reservation = temp_db.reserve_provider_spend(
            'anthropic', 100_000, run_id='run-p1')
        assert reservation['allowed']

        attempt = temp_db.begin_llm_attempt(
            run_id='run-p1', podcast_id=1, episode_id='ep1', phase_key='detect',
            invoking_pass=1, provider_key='anthropic',
            configured_model='test-recon-model')
        temp_db.finalize_llm_attempt(
            attempt, state='success', input_tokens=1_000_000, output_tokens=0)

        spend = temp_db.get_run_provider_spend('run-p1', 'anthropic')
        assert spend == 2_000_000
        assert temp_db.reconcile_provider_spend(reservation['reservation_id'], spend)

        status = temp_db.provider_budget_status('anthropic')
        assert status['spentMicrousd'] == 2_000_000
        assert status['reservedMicrousd'] == 0

    def test_uncertain_reconcile_still_supported_alongside_ledger_derivation(self, temp_db):
        """2.96.25 guarantee: a missing actual cost still reconciles to
        'uncertain', unaffected by ledger-derived reconcile elsewhere."""
        _enable_budget(temp_db)
        reservation = temp_db.reserve_provider_spend(
            'anthropic', 100_000, run_id='run-p2')
        assert reservation['allowed']

        assert temp_db.reconcile_provider_spend(reservation['reservation_id'], None)

        status = temp_db.provider_budget_status('anthropic')
        assert status['reservedMicrousd'] == 100_000
        assert status['spentMicrousd'] == 0

    def test_release_when_provider_never_attempted(self, temp_db):
        """2.96.25 guarantee: a reservation released before any attempt
        drops out of both spent and reserved totals."""
        _enable_budget(temp_db)
        reservation = temp_db.reserve_provider_spend(
            'anthropic', 100_000, run_id='run-p3')
        assert reservation['allowed']

        assert temp_db.release_provider_spend(reservation['reservation_id'])

        status = temp_db.provider_budget_status('anthropic')
        assert status['reservedMicrousd'] == 0
        assert status['spentMicrousd'] == 0
