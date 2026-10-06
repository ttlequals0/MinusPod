"""Failover HTTP surface (#806)."""
import os
import sys
import tempfile
from unittest.mock import patch

import pytest

from tests.app_bootstrap import authenticate_test_client

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='failover-api-test-'))

import api
import failover
from api.episodes import _run_stats_to_api


@pytest.fixture
def hdr(app_client):
    return authenticate_test_client(app_client)


@pytest.fixture
def configured(app_client, hdr):
    db = api.get_database()
    db.set_setting('failover_llm_enabled', 'true', is_default=False)
    db.set_setting('failover_llm_provider', 'openai-compatible', is_default=False)
    db.set_setting('failover_llm_detection_model', 'qwen3:8b', is_default=False)
    # The CSRF check below is only enforced once a password is set; match the
    # project convention for CSRF tests (e.g. test_podping_check_api.py).
    db.set_setting('app_password', 'pbkdf2:sha256:fake-for-tests', is_default=False)
    failover.invalidate_cache()
    yield db
    db.clear_setting('app_password')
    for t in failover.TARGETS:
        db.clear_setting(f'failover_state:{t}')
    failover.invalidate_cache()


def test_overview_shape(app_client, hdr, configured):
    body = app_client.get('/api/v1/failover').get_json()
    assert set(body['targets']) == {'llm-a', 'llm-b', 'transcriber'}
    assert body['targets']['llm-a']['configured'] is True
    assert body['targets']['transcriber']['configured'] is False
    assert set(body['probes']) >= {'llm-a', 'llm-failover', 'transcriber'}
    assert body['policy'] == {'probeIntervalMinutes': 5, 'recoveryProbes': 3}
    assert isinstance(body['events'], list)


def test_trigger_and_cancel(app_client, hdr, configured):
    with patch('failover.webhook_service.fire_failover_event'):
        r = app_client.post('/api/v1/failover/llm-a/trigger', json={'reason': 'drill'}, headers=hdr)
        assert r.status_code == 200, r.get_json()
        assert r.get_json()['state']['active'] is True
        assert r.get_json()['state']['source'] == 'manual'
        status = app_client.get('/api/v1/status').get_json()
        assert status['failover']['active'] == ['llm-a']
        r = app_client.post('/api/v1/failover/llm-a/cancel', headers=hdr)
        assert r.status_code == 200 and r.get_json()['state']['active'] is False
    events = app_client.get('/api/v1/failover').get_json()['events']
    assert [e['action'] for e in events[:2]] == ['cancel', 'trigger']
    assert events[0]['target'] == 'llm-a'


def test_trigger_unconfigured_is_409(app_client, hdr):
    r = app_client.post('/api/v1/failover/transcriber/trigger', headers=hdr)
    assert r.status_code == 409
    assert r.get_json()['error'] == 'failover_not_configured'


def test_trigger_disabled_provider_b_is_409(app_client, hdr):
    db = api.get_database()
    db.set_setting('secondary_provider_enabled', 'false', is_default=False)
    db.set_setting('secondary_provider', 'openai-compatible', is_default=False)
    failover.invalidate_cache()
    r = app_client.post('/api/v1/failover/llm-b/trigger', headers=hdr)
    assert r.status_code == 409
    assert r.get_json()['error'] == 'failover_target_disabled'


@pytest.mark.parametrize('body', [[], [1], 'reason', 1, 0, False, True])
def test_trigger_rejects_non_object_json(app_client, hdr, configured, body):
    with patch('failover.trigger') as trigger:
        r = app_client.post('/api/v1/failover/llm-a/trigger', json=body, headers=hdr)
    assert r.status_code == 400
    assert r.get_json()['error'] == 'request body must be a JSON object'
    trigger.assert_not_called()


@pytest.mark.parametrize('reason', [None, 1, False, [], {}])
def test_trigger_rejects_non_string_reason(app_client, hdr, configured, reason):
    with patch('failover.trigger') as trigger:
        r = app_client.post('/api/v1/failover/llm-a/trigger', json={'reason': reason}, headers=hdr)
    assert r.status_code == 400
    assert r.get_json()['error'] == 'reason must be a string'
    trigger.assert_not_called()


@pytest.mark.parametrize('body', ['{', '[', 'not-json', ' '])
def test_trigger_rejects_malformed_json(app_client, hdr, configured, body):
    with patch('failover.trigger') as trigger:
        r = app_client.post('/api/v1/failover/llm-a/trigger', data=body,
                            content_type='application/json', headers=hdr)
    assert r.status_code == 400
    assert r.get_json()['error'] == 'request body must be valid JSON'
    trigger.assert_not_called()


@pytest.mark.parametrize('body', ['', 'null', '{}'])
def test_trigger_accepts_default_reason(app_client, hdr, configured, body):
    with patch('failover.webhook_service.fire_failover_event'):
        r = app_client.post('/api/v1/failover/llm-a/trigger', data=body,
                            content_type='application/json', headers=hdr)
    assert r.status_code == 200
    assert r.get_json()['state']['reason'] == 'manual trigger'


@pytest.mark.parametrize('action', ['trigger', 'cancel'])
def test_manual_transition_reports_persistence_failure(app_client, hdr, configured, action):
    if action == 'cancel':
        with patch('failover.webhook_service.fire_failover_event'):
            assert failover.trigger(failover.TARGET_LLM_PRIMARY, 'drill', source='manual')
    before = failover.state(failover.TARGET_LLM_PRIMARY)
    with patch('failover.database.Database.transaction', side_effect=RuntimeError('transaction failed')):
        r = app_client.post(f'/api/v1/failover/llm-a/{action}', json={}, headers=hdr)
    assert r.status_code == 503
    assert r.get_json()['error'] == 'failover_transition_failed'
    assert failover.state(failover.TARGET_LLM_PRIMARY) == before


def test_repeated_manual_actions_stay_successful_without_duplicate_events(app_client, hdr, configured):
    with patch('failover.webhook_service.fire_failover_event') as fire:
        for action in ('trigger', 'trigger', 'cancel', 'cancel'):
            r = app_client.post(f'/api/v1/failover/llm-a/{action}', json={}, headers=hdr)
            assert r.status_code == 200
            assert r.get_json()['state']['active'] is (action == 'trigger')
    assert fire.call_count == 2


def test_unknown_target_404(app_client, hdr):
    assert app_client.post('/api/v1/failover/nope/trigger', headers=hdr).status_code == 404


def test_trigger_requires_csrf(app_client, configured):
    authenticate_test_client(app_client)
    r = app_client.post('/api/v1/failover/llm-a/trigger', json={})
    assert r.status_code == 403


def test_probe_now(app_client, hdr, configured):
    with patch('failover.probe_target', return_value={'reachable': True, 'status': 200, 'detail': 'ok'}):
        r = app_client.post('/api/v1/failover/probe', headers=hdr)
    assert r.status_code == 200
    assert r.get_json()['probes']['llm-a']['reachable'] is True
    assert r.get_json()['probes']['llm-a']['healthyStreak'] == 1


def test_probe_now_requeues_deferred_episode_without_maintenance_tick(app_client, hdr, configured):
    db = configured
    for t in failover.PROBE_TARGETS:
        db.clear_setting(f'failover_probe:{t}')
    failover.invalidate_cache()
    slug = 'failover-probe-feed'
    db.create_podcast(slug, 'https://example.com/feed.xml', title='Probe Requeue Test')
    db.upsert_episode(slug, 'ep-1', title='Episode 1', status='deferred',
                      original_url='https://example.com/ep1.mp3',
                      error_message='Deferred (llm endpoint unreachable)',
                      deferred_at='2999-01-01T00:00:00Z', deferred_service='llm')
    snapshot = {'detection': {'credential_slot': 'primary'}, 'review': {'credential_slot': 'primary'},
               'verification': {'credential_slot': 'primary'}, 'chapters': {'credential_slot': 'primary'}}
    try:
        with patch('main_app.background._resolve_route_snapshot', return_value=snapshot), \
                patch('failover.probe_target',
                      return_value={'reachable': True, 'status': 200, 'detail': 'ok'}):
            r = app_client.post('/api/v1/failover/probe', headers=hdr)
        assert r.status_code == 200
        assert db.get_episode(slug, 'ep-1')['status'] == 'pending'
    finally:
        db.delete_podcast(slug)
        for t in failover.PROBE_TARGETS:
            db.clear_setting(f'failover_probe:{t}')
        failover.invalidate_cache()


def test_models_slot_failover_uses_failover_client(app_client, hdr, configured):
    with patch('api.settings.create_client_for_provider') as make, \
            patch('api.settings._models_from_client', return_value=[{'id': 'qwen3:8b'}]):
        r = app_client.get('/api/v1/settings/models?provider=openai-compatible&slot=failover')
    assert r.status_code == 200
    assert make.call_args.kwargs['credential_slot'] == 'failover'


def test_models_slot_alias_b(app_client, hdr):
    with patch('api.settings.create_client_for_provider'), \
            patch('api.settings._models_from_client', return_value=[]):
        assert app_client.get('/api/v1/settings/models?provider=ollama&slot=b').status_code == 200


def test_failover_llm_test_connection(app_client, hdr, configured):
    with patch('api.providers._probe_models_endpoint', return_value={'ok': True, 'reachable': True, 'status': 200, 'detail': 'ok'}) as p:
        r = app_client.post('/api/v1/settings/providers/failover/test-connection',
                            json={'baseUrl': 'http://127.0.0.1:11434/v1'}, headers=hdr)
    assert r.status_code == 200 and r.get_json()['ok'] is True
    assert p.call_args.args[1] == ''   # unsaved URL never carries the saved key


def test_failover_whisper_test_connection(app_client, hdr):
    with patch('api.providers.transcriber.probe_transcription_endpoint',
               return_value={'ok': True, 'reachable': True, 'status': 200, 'detail': 'ok'}), \
            patch('api.providers.transcriber.probe_whisper_health', return_value={'available': False}):
        r = app_client.post('/api/v1/settings/providers/failover-whisper/test-connection',
                            json={'baseUrl': 'http://127.0.0.1:8001/v1', 'model': 'whisper-1'}, headers=hdr)
    assert r.status_code == 200 and r.get_json()['ok'] is True


def test_targets_report_whether_the_original_is_enabled(app_client, hdr, configured):
    configured.set_setting('secondary_provider_enabled', 'false', is_default=False)
    failover.invalidate_cache()
    targets = app_client.get('/api/v1/failover').get_json()['targets']
    assert targets['llm-a']['enabled'] is True
    assert targets['transcriber']['enabled'] is True
    assert targets['llm-b']['enabled'] is False
    configured.set_setting('secondary_provider_enabled', 'true', is_default=False)
    configured.set_setting('secondary_provider', 'openai-compatible', is_default=False)
    failover.invalidate_cache()
    try:
        assert app_client.get('/api/v1/failover').get_json()['targets']['llm-b']['enabled'] is True
    finally:
        configured.set_setting('secondary_provider_enabled', 'false', is_default=False)
        configured.clear_setting('secondary_provider')
        failover.invalidate_cache()


def test_run_stats_expose_failover_usage():
    out = _run_stats_to_api({'mode': 'auto', 'failover': {'llm': ['primary', 'secondary'],
                                                          'whisper': True}})
    assert out['failover'] == {'llm': ['llm-a', 'llm-b'], 'whisper': True}
    assert 'failover' not in _run_stats_to_api({'mode': 'auto'})
