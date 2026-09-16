from tests.app_bootstrap import bootstrap

bootstrap('branch_audit_')

import pytest

from llm_client import LLMResponse, ProviderRateLimitedError, invalidate_provider_cache
from main_app.processing import _required_providers_for_admission, _resolve_route_snapshot
from tests.unit.test_llm_call_window_loss import _FakeLLMClient, _window_call


def configure_routes(db):
    for key, value in {
        'llm_provider': 'anthropic',
        'claude_model': 'test-model',
        'secondary_provider_enabled': 'true',
        'secondary_provider': 'openai-compatible',
        'secondary_provider_base_url': 'https://secondary.example/v1',
        'detection_provider': 'secondary',
        'verification_provider': 'primary',
        'chapters_provider': 'secondary',
        'review_provider': 'same_as_pass',
        'enable_ad_review': 'true',
    }.items():
        db.set_setting(key, value)
    invalidate_provider_cache()


def test_review_snapshot_preserves_detection_account(temp_db):
    configure_routes(temp_db)
    snapshot = _resolve_route_snapshot()
    assert snapshot['review']['credential_slot'] == 'secondary'
    assert snapshot['review']['base_url'] == snapshot['detection']['base_url']


@pytest.mark.parametrize('projected', [False, True])
def test_skipped_verification_does_not_block_admission(temp_db, projected):
    configure_routes(temp_db)
    temp_db.create_podcast('audit-show', 'https://example.com/feed.xml', 'Audit')
    temp_db.update_podcast('audit-show', skip_second_pass=1)
    snapshot = _resolve_route_snapshot()
    snapshot['review'] = dict(snapshot['detection'])
    row = {'skip_second_pass': 1} if projected else None
    required = _required_providers_for_admission(
        'audit-show', snapshot=snapshot, queue_row=row)
    assert ('anthropic', 'primary') not in required


def begin(db):
    return db.begin_llm_attempt(
        run_id='audit-run', podcast_id=None, episode_id=None,
        phase_key='detection', invoking_pass=1, provider_key='openai-compatible',
        configured_model='audit-unpriced-model', credential_slot='primary')


def test_success_without_usage_is_incomplete_not_free(temp_db):
    temp_db.finalize_llm_attempt(begin(temp_db), state='success')
    assert temp_db.get_run_usage_totals('audit-run')['has_unknown_cost']
    items, total = temp_db.get_model_usage_stats()
    assert total == 1
    assert items[0]['unknownCostCount'] == 1
    assert temp_db.run_provider_spend_is_incomplete('audit-run', 'openai-compatible')


def test_cost_only_failure_is_included_in_spend(temp_db):
    temp_db.finalize_llm_attempt(
        begin(temp_db), state='failure', provider_reported_cost_usd=0.125)
    assert float(temp_db.get_run_usage_totals('audit-run')['cost_usd']) == 0.125
    assert temp_db.get_run_provider_spend('audit-run', 'openai-compatible') == 125000
    items, total = temp_db.get_model_usage_stats()
    assert total == 1
    assert float(items[0]['knownCostUsd']) == 0.125


def test_reasoning_retry_obeys_manual_request_cap(temp_db):
    temp_db.set_setting('provider_requests_per_min', '1')
    client = _FakeLLMClient([
        LLMResponse(content='', model='m', finish_reason='length',
                    reasoning_exhausted=True,
                    usage={'input_tokens': 100, 'output_tokens': 4096}),
        LLMResponse(content='[]', model='m',
                    usage={'input_tokens': 100, 'output_tokens': 10}),
    ])
    response, error = _window_call(client)
    assert client.calls == 1
    assert response is None
    assert isinstance(error, ProviderRateLimitedError)
