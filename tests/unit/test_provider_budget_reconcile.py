"""Provider-budget reconciliation retains collected token totals."""

import threading
from contextlib import ExitStack
from unittest.mock import patch

from tests.app_bootstrap import bootstrap

bootstrap('provider_budget_test_')

import run_context
from config import normalize_model_key
from llm_client import (
    get_episode_token_totals,
    get_last_episode_token_totals,
)
import main_app.processing as processing


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


_SEGMENTS = [{'start': 0.0, 'end': 5.0, 'text': 'hello'}]


def _admissions_for(provider_keys, deny=None):
    """One allowed admission per provider_key (id 'resv-<key>'); `deny`
    marks one key as refused, matching reserve_provider_spend's shape."""
    admissions = {}
    for key in provider_keys:
        if key == deny:
            admissions[key] = {
                'allowed': False, 'reservation_id': None, 'reason': 'daily_budget'}
        else:
            admissions[key] = {'allowed': True, 'reservation_id': f'resv-{key}'}
    return admissions


def _run_pipeline(required_providers, admissions, spends=None, incomplete_spend=False):
    """Drives process_episode through a full non-skip-detection run with
    every ad-detection/verification stage stubbed to a no-op, isolating the
    provider-reservation lifecycle at Stage 3 and finalize. Mirrors
    test_skip_second_pass.py's harness."""
    podcast_row = {'id': 1, 'slug': 'budget-feed', 'description': None,
                   'tags': None, 'dai_platform': None,
                   'passthrough_enabled': None, 'skip_ad_detection': None,
                   'skip_second_pass': None}
    with ExitStack() as stack:
        p = lambda *a, **k: stack.enter_context(patch.object(*a, **k))
        db = p(processing, 'db')
        p(processing, 'status_service')
        storage = p(processing, 'storage')
        audio_processor = p(processing, 'audio_processor')
        p(processing.ad_detector, 'get_model', return_value='test-model')
        p(processing.ad_detector, 'get_verification_model', return_value='test-model')
        p(processing, 'start_episode_token_tracking')
        p(processing, 'get_available_memory_gb', return_value=None)
        p(processing, 'get_min_cut_confidence', return_value=0.8)
        p(processing, '_download_and_transcribe',
          return_value=('/tmp/budget.mp3', _SEGMENTS))
        p(processing, '_run_differential_fetch', return_value=None)
        p(processing, '_run_audio_analysis', return_value=None)
        p(processing, 'load_positional_prior', return_value=None)
        p(processing, '_detect_ads_first_pass', return_value=([], 0, None))
        p(processing, '_refine_and_validate', return_value=([], []))
        p(processing, '_run_ad_reviewer', return_value=([], []))
        p(processing, '_snap_terminal_starts', return_value=[])
        p(processing, '_complete_cut_tails', return_value=[])
        local_ap_cls = p(processing, 'AudioProcessor')
        p(processing, '_run_verification_pass',
          return_value=(0, [], [], [], '/tmp/cut.mp3', 0, True, 0))
        p(processing, '_generate_assets')
        p(processing, '_finalize_episode')
        p(processing.shutil, 'move')
        p(processing.os, 'unlink')
        p(processing.os.path, 'exists', return_value=False)
        p(processing, '_required_providers_for_admission',
          return_value=required_providers)
        # Fallback only: used when required_providers is None (not exercised
        # by these cases), kept fixed so it never masks a real assertion.
        p(processing, 'get_effective_provider', return_value='legacy-provider')

        db.get_episode.return_value = {}
        db.get_podcast_by_slug.return_value = podcast_row
        db.get_setting.return_value = 'false'
        db.get_all_settings.return_value = {}
        db.reserve_provider_spend.side_effect = (
            lambda provider_key, cost, run_id=None: admissions[provider_key])
        db.run_provider_spend_is_incomplete.return_value = incomplete_spend
        if spends is not None:
            db.get_run_provider_spend.side_effect = (
                lambda run_id, provider_key: spends[provider_key])
        audio_processor.get_audio_duration.return_value = 100.0
        local_ap = local_ap_cls.return_value
        local_ap.process_episode.return_value = ('/tmp/cut.mp3', [])
        local_ap.get_audio_duration.return_value = 100.0
        storage.get_episode_path.return_value = '/tmp/final.mp3'
        result = processing.process_episode(
            'budget-feed', 'ep1', 'https://example.com/ep1.mp3')
    return {'result': result, 'db': db}


class TestProviderScopedReservations:
    """A run's phases can route to distinct provider TYPES (cross-provider
    secondary routing); each distinct provider_key must get its own
    reservation and be reconciled against its own ledger spend, not folded
    into (or excluded from) the primary's."""

    def test_cross_type_routing_reserves_and_reconciles_each_provider(self):
        required = [('anthropic', 'primary'), ('openrouter', 'secondary')]
        admissions = _admissions_for(['anthropic', 'openrouter'])
        spends = {'anthropic': 500_000, 'openrouter': 250_000}

        outcome = _run_pipeline(required, admissions, spends)
        db = outcome['db']

        assert outcome['result'] is True
        reserved_keys = {c.args[0] for c in db.reserve_provider_spend.call_args_list}
        assert reserved_keys == {'anthropic', 'openrouter'}
        db.reconcile_provider_spend.assert_any_call('resv-anthropic', 500_000)
        db.reconcile_provider_spend.assert_any_call('resv-openrouter', 250_000)
        assert db.reconcile_provider_spend.call_count == 2

    def test_same_type_phases_collapse_to_one_reservation(self):
        # Two phases both routed to anthropic (e.g. detection + verification):
        # one shared provider_key, one reservation, combined spend.
        required = [('anthropic', 'primary'), ('anthropic', 'primary')]
        admissions = _admissions_for(['anthropic'])
        spends = {'anthropic': 750_000}

        outcome = _run_pipeline(required, admissions, spends)
        db = outcome['db']

        assert outcome['result'] is True
        assert db.reserve_provider_spend.call_count == 1
        assert db.reserve_provider_spend.call_args.args[0] == 'anthropic'
        db.reconcile_provider_spend.assert_called_once_with('resv-anthropic', 750_000)

    def test_incomplete_cost_reconciles_conservatively_at_the_estimate(self):
        # A run with an unknown-cost call must not settle on the known-only
        # subtotal; reconcile with None keeps the reservation estimate.
        required = [('anthropic', 'primary')]
        admissions = _admissions_for(['anthropic'])
        spends = {'anthropic': 750_000}

        outcome = _run_pipeline(required, admissions, spends, incomplete_spend=True)
        db = outcome['db']

        assert outcome['result'] is True
        db.reconcile_provider_spend.assert_called_once_with('resv-anthropic', None)

    def test_second_provider_denial_releases_the_first_reservation(self):
        # anthropic admits, openrouter's budget is exhausted: the whole run
        # must be refused and anthropic's reservation must not be left
        # dangling in 'reserved' state.
        required = [('anthropic', 'primary'), ('openrouter', 'secondary')]
        admissions = _admissions_for(['anthropic', 'openrouter'], deny='openrouter')

        outcome = _run_pipeline(required, admissions)
        db = outcome['db']

        assert outcome['result'] is False
        reserved_keys = [c.args[0] for c in db.reserve_provider_spend.call_args_list]
        assert reserved_keys == ['anthropic', 'openrouter']
        db.release_provider_spend.assert_called_once_with('resv-anthropic')
        db.reconcile_provider_spend.assert_not_called()
