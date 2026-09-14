"""Tests for the llm_call_usage ledger begin/finalize API (checkpoint 03).

Covers single-transaction counter derivation: finalize_llm_attempt must be
the only writer of token_usage/stats counters for ledgered calls.
"""
from decimal import Decimal

import pytest

from config import normalize_model_key


def _seed_price(db, model_id, input_cost, output_cost):
    db.upsert_fetched_pricing([{
        'match_key': normalize_model_key(model_id),
        'raw_model_id': model_id,
        'display_name': model_id,
        'input_cost_per_mtok': input_cost,
        'output_cost_per_mtok': output_cost,
    }], source='litellm')


def _begin(db, model_id, provider_key='anthropic'):
    return db.begin_llm_attempt(
        run_id='run-1', podcast_id=1, episode_id='ep1', phase_key='detect',
        invoking_pass=1, provider_key=provider_key, configured_model=model_id)


class TestBeginLlmAttempt:
    def test_inserts_in_flight_row_and_returns_new_uuid(self, temp_db):
        attempt_id = _begin(temp_db, 'claude-sonnet-5')

        row = temp_db.get_connection().execute(
            "SELECT * FROM llm_call_usage WHERE attempt_id = ?", (attempt_id,)
        ).fetchone()
        assert row is not None
        assert row['state'] == 'in_flight'
        assert row['run_id'] == 'run-1'
        assert row['provider_key'] == 'anthropic'
        assert row['configured_model'] == 'claude-sonnet-5'
        assert row['phase_key'] == 'detect'

        other_id = _begin(temp_db, 'claude-sonnet-5')
        assert other_id != attempt_id


class TestFinalizeLlmAttempt:
    def test_success_increments_counters_once(self, temp_db):
        _seed_price(temp_db, 'test-model-a', 3.0, 15.0)
        attempt_id = _begin(temp_db, 'test-model-a')

        cost = temp_db.finalize_llm_attempt(
            attempt_id, state='success', returned_model='test-model-a',
            input_tokens=1_000_000, output_tokens=1_000_000)

        assert cost == pytest.approx(18.0)
        summary = temp_db.get_token_usage_summary()
        assert summary['totalInputTokens'] == 1_000_000
        assert summary['totalOutputTokens'] == 1_000_000
        assert summary['totalCost'] == pytest.approx(18.0)
        assert summary['models'][0]['callCount'] == 1

        row = temp_db.get_connection().execute(
            "SELECT state, cost_usd, cost_source, finalized_at, returned_model "
            "FROM llm_call_usage WHERE attempt_id = ?", (attempt_id,)
        ).fetchone()
        assert row['state'] == 'success'
        assert row['cost_source'] == 'estimated'
        assert Decimal(row['cost_usd']) == Decimal('18')
        assert row['finalized_at'] is not None
        assert row['returned_model'] == 'test-model-a'

    def test_two_attempts_same_model_are_not_deduped_and_both_sum(self, temp_db):
        _seed_price(temp_db, 'test-model-b', 1.0, 2.0)
        a1 = _begin(temp_db, 'test-model-b')
        a2 = _begin(temp_db, 'test-model-b')

        temp_db.finalize_llm_attempt(a1, state='success', input_tokens=1_000_000, output_tokens=0)
        temp_db.finalize_llm_attempt(a2, state='success', input_tokens=1_000_000, output_tokens=0)

        count = temp_db.get_connection().execute(
            "SELECT COUNT(*) AS n FROM llm_call_usage WHERE configured_model = 'test-model-b'"
        ).fetchone()['n']
        assert count == 2

        summary = temp_db.get_token_usage_summary()
        assert summary['totalInputTokens'] == 2_000_000
        assert summary['totalCost'] == pytest.approx(2.0)
        assert summary['models'][0]['callCount'] == 2

    def test_billed_failure_counts_once(self, temp_db):
        _seed_price(temp_db, 'test-model-c', 3.0, 15.0)
        attempt_id = _begin(temp_db, 'test-model-c')

        cost = temp_db.finalize_llm_attempt(
            attempt_id, state='failure', input_tokens=500_000, output_tokens=0)

        assert cost == pytest.approx(1.5)
        summary = temp_db.get_token_usage_summary()
        assert summary['totalInputTokens'] == 500_000
        assert summary['totalCost'] == pytest.approx(1.5)
        row = temp_db.get_connection().execute(
            "SELECT state FROM llm_call_usage WHERE attempt_id = ?", (attempt_id,)
        ).fetchone()
        assert row['state'] == 'failure'

    def test_cancelled_writes_no_counters(self, temp_db):
        _seed_price(temp_db, 'test-model-d', 3.0, 15.0)
        attempt_id = _begin(temp_db, 'test-model-d')

        cost = temp_db.finalize_llm_attempt(attempt_id, state='cancelled')

        assert cost == 0.0
        summary = temp_db.get_token_usage_summary()
        assert summary['totalInputTokens'] == 0
        assert summary['totalCost'] == 0.0
        row = temp_db.get_connection().execute(
            "SELECT state, cost_usd FROM llm_call_usage WHERE attempt_id = ?", (attempt_id,)
        ).fetchone()
        assert row['state'] == 'cancelled'
        assert row['cost_usd'] is None

    def test_failure_with_unknown_tokens_writes_no_counters(self, temp_db):
        attempt_id = _begin(temp_db, 'test-model-nofail')

        cost = temp_db.finalize_llm_attempt(attempt_id, state='failure')

        assert cost == 0.0
        summary = temp_db.get_token_usage_summary()
        assert summary['totalInputTokens'] == 0
        row = temp_db.get_connection().execute(
            "SELECT state FROM llm_call_usage WHERE attempt_id = ?", (attempt_id,)
        ).fetchone()
        assert row['state'] == 'failure'

    def test_unknown_price_records_null_cost_and_token_only_counters(self, temp_db):
        attempt_id = _begin(temp_db, 'uncatalogued-ledger-model')

        cost = temp_db.finalize_llm_attempt(
            attempt_id, state='success', input_tokens=1000, output_tokens=500)

        assert cost == 0.0
        row = temp_db.get_connection().execute(
            "SELECT cost_source, cost_usd FROM llm_call_usage WHERE attempt_id = ?", (attempt_id,)
        ).fetchone()
        assert row['cost_source'] == 'unknown'
        assert row['cost_usd'] is None

        summary = temp_db.get_token_usage_summary()
        assert summary['totalInputTokens'] == 1000
        assert summary['totalOutputTokens'] == 500
        assert summary['totalCost'] == 0.0

    def test_explicit_zero_priced_model_is_not_labeled_unknown(self, temp_db):
        temp_db.merge_model_pricing_overrides({
            'free-ledger-model': {'inputCostPerMtok': 0.0, 'outputCostPerMtok': 0.0},
        })
        attempt_id = _begin(temp_db, 'free-ledger-model', provider_key='ollama')

        cost = temp_db.finalize_llm_attempt(
            attempt_id, state='success', input_tokens=1000, output_tokens=500)

        assert cost == 0.0
        row = temp_db.get_connection().execute(
            "SELECT cost_source, cost_usd FROM llm_call_usage WHERE attempt_id = ?", (attempt_id,)
        ).fetchone()
        assert row['cost_source'] == 'explicit_zero'
        assert Decimal(row['cost_usd']) == Decimal('0')

        summary = temp_db.get_token_usage_summary()
        assert summary['totalInputTokens'] == 1000

    def test_provider_reported_cost_is_used_verbatim(self, temp_db):
        attempt_id = _begin(temp_db, 'test-model-f')

        cost = temp_db.finalize_llm_attempt(
            attempt_id, state='success', input_tokens=1000, output_tokens=500,
            provider_reported_cost_usd=0.0042)

        assert cost == pytest.approx(0.0042)
        row = temp_db.get_connection().execute(
            "SELECT cost_source, cost_usd FROM llm_call_usage WHERE attempt_id = ?", (attempt_id,)
        ).fetchone()
        assert row['cost_source'] == 'provider_reported'
        assert Decimal(row['cost_usd']) == Decimal('0.0042')

        summary = temp_db.get_token_usage_summary()
        assert summary['totalCost'] == pytest.approx(0.0042)

    def test_unknown_attempt_id_is_a_noop(self, temp_db):
        cost = temp_db.finalize_llm_attempt('does-not-exist', state='success',
                                             input_tokens=100, output_tokens=100)
        assert cost == 0.0
