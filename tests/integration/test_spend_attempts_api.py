"""Integration tests for GET /stats/spend/attempts: the ledger rows behind one
run's spend, which the Stats Incomplete chip links to.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from tests.app_bootstrap import bootstrap

bootstrap('spend_attempts_test_')

from config import normalize_model_key  # noqa: E402


def _authed(client):
    with client.session_transaction() as sess:
        sess['authenticated'] = True
    client.get('/api/v1/auth/status')


def _seed_price(db, model_id, input_cost, output_cost):
    db.upsert_fetched_pricing([{
        'match_key': normalize_model_key(model_id),
        'raw_model_id': model_id,
        'display_name': model_id,
        'input_cost_per_mtok': input_cost,
        'output_cost_per_mtok': output_cost,
    }], source='litellm')


def _call(db, *, run_id, podcast_id, episode_id, provider_key, configured_model,
          phase_key='detect', state='success', input_tokens=1_000_000, output_tokens=0):
    attempt_id = db.begin_llm_attempt(
        run_id=run_id, podcast_id=podcast_id, episode_id=episode_id,
        phase_key=phase_key, invoking_pass=1, provider_key=provider_key,
        configured_model=configured_model)
    return db.finalize_llm_attempt(
        attempt_id, state=state, input_tokens=input_tokens, output_tokens=output_tokens)


def _seed_run(db):
    podcast_id = db.create_podcast('spend-pod', 'https://example.com/feed.xml', 'Spend Pod')
    db.upsert_episode('spend-pod', 'ep1', original_url='https://example.com/e.mp3',
                      title='Ep1', status='processed')
    _seed_price(db, 'priced-model', 3.0, 0.0)
    _call(db, run_id='run-a', podcast_id=podcast_id, episode_id='ep1',
          provider_key='openai', configured_model='priced-model', phase_key='detection')
    # No catalog price, so this attempt is billable with an unknown cost.
    _call(db, run_id='run-a', podcast_id=podcast_id, episode_id='ep1',
          provider_key='openai', configured_model='unpriced-model', phase_key='review')
    return podcast_id


def test_lists_the_attempts_behind_a_run_with_the_unknown_cost_marked(app_client, temp_db):
    _authed(app_client)
    _seed_run(temp_db)

    body = app_client.get('/api/v1/stats/spend/attempts?runId=run-a').get_json()
    assert body['total'] == 2
    assert body['unknownCostCount'] == 1
    assert body['knownCostUsd'] == '3.0'
    assert body['truncated'] is False
    by_phase = {a['phase']: a for a in body['attempts']}
    assert by_phase['detection']['costUsd'] == '3.0'
    assert by_phase['detection']['status'] == 'success'
    assert by_phase['detection']['inputTokens'] == 1_000_000
    assert by_phase['detection']['credentialSlot'] == 'primary'
    assert by_phase['review']['costUsd'] is None
    assert by_phase['review']['model'] == 'unpriced-model'
    assert by_phase['review']['finalizedAt']


def test_provider_filter_scopes_the_listing(app_client, temp_db):
    _authed(app_client)
    podcast_id = _seed_run(temp_db)
    _call(temp_db, run_id='run-a', podcast_id=podcast_id, episode_id='ep1',
          provider_key='anthropic', configured_model='priced-model', phase_key='chapters')

    body = app_client.get(
        '/api/v1/stats/spend/attempts?runId=run-a&provider=anthropic').get_json()
    assert body['provider'] == 'anthropic'
    assert [a['provider'] for a in body['attempts']] == ['anthropic']


def test_unknown_run_returns_an_empty_listing(app_client, temp_db):
    _authed(app_client)
    body = app_client.get('/api/v1/stats/spend/attempts?runId=nope').get_json()
    assert body['attempts'] == []
    assert body['total'] == 0


def test_missing_run_id_is_rejected(app_client, temp_db):
    _authed(app_client)
    assert app_client.get('/api/v1/stats/spend/attempts').status_code == 400


def test_episode_scope_covers_every_run_of_that_episode(app_client, temp_db):
    _authed(app_client)
    podcast_id = _seed_run(temp_db)
    _call(temp_db, run_id='run-b', podcast_id=podcast_id, episode_id='ep1',
          provider_key='openai', configured_model='priced-model', phase_key='verification')

    body = app_client.get(
        '/api/v1/stats/spend/attempts?slug=spend-pod&episodeId=ep1').get_json()
    assert body['runId'] is None
    assert body['episodeId'] == 'ep1'
    assert {a['phase'] for a in body['attempts']} == {'detection', 'review', 'verification'}


def test_unknown_feed_is_a_404(app_client, temp_db):
    _authed(app_client)
    resp = app_client.get('/api/v1/stats/spend/attempts?slug=nope&episodeId=ep1')
    assert resp.status_code == 404
