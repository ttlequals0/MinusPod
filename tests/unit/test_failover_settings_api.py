"""Settings surface for provider failover (#806)."""
import os
import sys
import tempfile
from unittest.mock import patch

import pytest

from tests.app_bootstrap import authenticate_test_client

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='failover-settings-test-'))

from api import get_database

FAILOVER_PAYLOAD = {
    'failoverLlmEnabled': True,
    'failoverLlmProvider': 'openai-compatible',
    'failoverLlmBaseUrl': 'http://127.0.0.1:11434/v1',
    'failoverLlmTimeoutSeconds': 300,
    'failoverLlmMaxRetries': 1,
    'failoverLlmDetectionModel': 'qwen3:8b',
    'failoverLlmReviewModel': '',
    'failoverWhisperEnabled': True,
    'failoverWhisperBackend': 'openai-api',
    'failoverWhisperApiBaseUrl': 'http://127.0.0.1:8001/v1',
    'failoverWhisperApiModel': 'whisper-1',
    'failoverWhisperApiTimeoutSeconds': 120,
    'failoverProbeIntervalMinutes': 2,
    'failoverRecoveryProbes': 4,
    'providerATimeoutSeconds': 90,
    'providerAMaxRetries': 2,
    'providerBTimeoutSeconds': 600,
    'providerBMaxRetries': 1,
    'whisperMaxAttempts': 3,
}


_WRITTEN_KEYS = (
    'failover_llm_enabled', 'failover_llm_provider', 'failover_llm_base_url',
    'failover_llm_timeout_seconds', 'failover_llm_max_retries',
    'failover_llm_detection_model', 'failover_llm_review_model',
    'failover_whisper_enabled', 'failover_whisper_backend',
    'failover_whisper_api_base_url', 'failover_whisper_api_model',
    'failover_whisper_api_timeout_seconds',
    'failover_whisper_max_attempts',
    'failover_probe_interval_minutes', 'failover_recovery_probes',
    'llm_timeout_seconds', 'llm_max_retries',
    'secondary_llm_timeout_seconds', 'secondary_llm_max_retries',
    'whisper_max_attempts',
    'secondary_provider_enabled', 'secondary_provider',
    'secondary_provider_base_url',
    'secondary_provider_requests_per_min', 'detection_provider',
    'failover_state:llm:primary', 'reviewer_calibration_on_change',
)


@pytest.fixture
def hdr(app_client):
    db = get_database()
    # Prevent the provider-change self-test from opening a shared circuit breaker.
    db.set_setting('reviewer_calibration_on_change', 'false', is_default=False)
    token = authenticate_test_client(app_client)
    yield token
    for key in _WRITTEN_KEYS:
        db.clear_setting(key)


def test_put_and_get_round_trip(app_client, hdr):
    r = app_client.put('/api/v1/settings/ad-detection', json=FAILOVER_PAYLOAD, headers=hdr)
    assert r.status_code == 200, r.get_json()
    body = app_client.get('/api/v1/settings').get_json()
    assert body['failoverLlmEnabled']['value'] is True
    assert body['failoverLlmProvider']['value'] == 'openai-compatible'
    assert body['failoverLlmTimeoutSeconds']['value'] == 300
    assert body['failoverLlmDetectionModel']['value'] == 'qwen3:8b'
    assert body['failoverLlmReviewModel']['value'] == ''
    assert body['failoverWhisperBackend']['value'] == 'openai-api'
    assert body['failoverProbeIntervalMinutes']['value'] == 2
    assert body['failoverRecoveryProbes']['value'] == 4
    assert body['providerATimeoutSeconds']['value'] == 90
    assert body['providerBMaxRetries']['value'] == 1
    assert body['whisperMaxAttempts']['value'] == 3
    assert body['failoverLlmApiKeyConfigured'] is False
    assert body['defaults']['failoverProbeIntervalMinutes'] == 5
    assert body['defaults']['failoverRecoveryProbes'] == 3


@pytest.mark.parametrize('payload', [
    {'failoverLlmProvider': 'bogus'},
    {'failoverLlmTimeoutSeconds': 5},
    {'failoverLlmTimeoutSeconds': 4000},
    {'failoverLlmMaxRetries': -1},
    {'failoverLlmMaxRetries': 11},
    {'failoverWhisperBackend': 'remote'},
    {'failoverWhisperApiTimeoutSeconds': 10},
    {'failoverProbeIntervalMinutes': 0},
    {'failoverProbeIntervalMinutes': 61},
    {'failoverRecoveryProbes': 0},
    {'providerATimeoutSeconds': 'fast'},
    {'whisperMaxAttempts': 0},
    {'failoverLlmBaseUrl': 'http://user:pw@example.com/v1'},
])
def test_put_rejects_bad_values(app_client, hdr, payload):
    r = app_client.put('/api/v1/settings/ad-detection', json=payload, headers=hdr)
    assert r.status_code == 400


@pytest.mark.parametrize('payload', [
    {'detectionProvider': []},
    {'verificationProvider': {}},
])
def test_stage_provider_aliases_reject_unhashable_values(app_client, hdr, payload):
    r = app_client.put('/api/v1/settings/ad-detection', json=payload, headers=hdr)
    assert r.status_code == 400


@pytest.mark.parametrize(('key', 'value'), [
    ('failoverLlmEnabled', 'false'),
    ('failoverWhisperEnabled', 1),
    ('failoverLlmTimeoutSeconds', 90.5),
    ('failoverLlmMaxRetries', True),
    ('providerATimeoutSeconds', 90.5),
    ('failoverWhisperApiTimeoutSeconds', True),
    ('failoverProbeIntervalMinutes', 2.5),
    ('failoverRecoveryProbes', False),
    ('whisperMaxAttempts', False),
    ('whisperMaxAttempts', True),
    ('whisperMaxAttempts', 2.5),
    ('whisperMaxAttempts', '3'),
    ('failoverLlmProvider', []),
    ('failoverLlmDetectionModel', {}),
    ('failoverWhisperModel', None),
    ('failoverWhisperApiModel', []),
    ('failoverWhisperLanguage', {}),
    ('failoverLlmApiKey', 5),
    ('failoverWhisperApiKey', []),
    ('failoverWhisperApiBaseUrl', {}),
])
def test_invalid_failover_types_do_not_partially_write(app_client, hdr, key, value):
    db = get_database()
    db.set_setting('failover_llm_detection_model', 'existing-model', is_default=False)
    r = app_client.put('/api/v1/settings/ad-detection', json={
        'failoverLlmDetectionModel': 'replacement-model', key: value,
    }, headers=hdr)
    assert r.status_code == 400
    assert db.get_setting('failover_llm_detection_model') == 'existing-model'


def test_blank_timeout_clears_to_type_default(app_client, hdr):
    app_client.put('/api/v1/settings/ad-detection',
                   json={'providerATimeoutSeconds': 90}, headers=hdr)
    r = app_client.put('/api/v1/settings/ad-detection',
                       json={'providerATimeoutSeconds': None}, headers=hdr)
    assert r.status_code == 200
    body = app_client.get('/api/v1/settings').get_json()
    assert body['providerATimeoutSeconds']['value'] is None


@pytest.mark.parametrize('value', [1, 10])
def test_standby_attempts_override_round_trip_and_omission(app_client, hdr, value):
    r = app_client.put('/api/v1/settings/ad-detection',
                       json={'failoverWhisperMaxAttempts': value}, headers=hdr)
    assert r.status_code == 200, r.get_json()
    r = app_client.put('/api/v1/settings/ad-detection', json={'whisperMaxAttempts': 3}, headers=hdr)
    assert r.status_code == 200
    body = app_client.get('/api/v1/settings').get_json()
    assert body['failoverWhisperMaxAttempts']['value'] == value
    assert body['defaults']['failoverWhisperMaxAttempts'] is None


@pytest.mark.parametrize('value', [None, ''])
def test_standby_attempts_clear_to_inheritance(app_client, hdr, value):
    db = get_database()
    db.set_setting('failover_whisper_max_attempts', '5', is_default=False)
    r = app_client.put('/api/v1/settings/ad-detection',
                       json={'failoverWhisperMaxAttempts': value}, headers=hdr)
    assert r.status_code == 200, r.get_json()
    assert db.get_setting('failover_whisper_max_attempts') is None
    assert app_client.get('/api/v1/settings').get_json()['failoverWhisperMaxAttempts']['value'] is None


@pytest.mark.parametrize('value', [0, 11, True, False, 2.5, '3', [], {}])
def test_invalid_standby_attempts_are_atomic(app_client, hdr, value):
    db = get_database()
    db.set_setting('failover_llm_detection_model', 'existing-model', is_default=False)
    r = app_client.put('/api/v1/settings/ad-detection', json={
        'failoverLlmDetectionModel': 'replacement-model', 'failoverWhisperMaxAttempts': value,
    }, headers=hdr)
    assert r.status_code == 400
    assert db.get_setting('failover_llm_detection_model') == 'existing-model'


def test_provider_b_aliases_write_secondary_keys(app_client, hdr):
    r = app_client.put('/api/v1/settings/ad-detection', json={
        'providerBEnabled': True, 'providerB': 'ollama',
        'providerBBaseUrl': 'http://127.0.0.1:11434',
        'providerBRequestsPerMin': 7,
    }, headers=hdr)
    assert r.status_code == 200, r.get_json()
    body = app_client.get('/api/v1/settings').get_json()
    assert body['secondaryProviderEnabled']['value'] is True
    assert body['providerBEnabled']['value'] is True
    assert body['secondaryProvider']['value'] == 'ollama'
    assert body['providerB']['value'] == 'ollama'
    assert body['secondaryProviderRequestsPerMin']['value'] == 7
    assert body['providerBRequestsPerMin']['value'] == 7


def test_slot_aliases_accepted_for_stage_routing(app_client, hdr):
    app_client.put('/api/v1/settings/ad-detection', json={
        'providerBEnabled': True, 'providerB': 'ollama'}, headers=hdr)
    r = app_client.put('/api/v1/settings/ad-detection',
                       json={'detectionProvider': 'b'}, headers=hdr)
    assert r.status_code == 200, r.get_json()
    body = app_client.get('/api/v1/settings').get_json()
    assert body['detectionProvider']['value'] == 'secondary'


def test_disabling_failover_clears_active_state(app_client, hdr):
    db = get_database()
    db.set_setting('failover_state:llm:primary',
                   '{"active": true, "source": "manual", "since": "x", "reason": "r"}',
                   is_default=False)
    with patch('failover.webhook_service.fire_failover_event') as fire:
        r = app_client.put('/api/v1/settings/ad-detection',
                           json={'failoverLlmEnabled': False}, headers=hdr)
    assert r.status_code == 200
    assert db.get_setting('failover_state:llm:primary') is None
    fire.assert_called_once_with('cancel', 'llm:primary', 'manual', None)
    event = db.get_failover_events(1)[0]
    assert (event['target'], event['action'], event['source']) == ('llm:primary', 'cancel', 'manual')


def test_disabling_provider_b_cancels_active_secondary_failover(app_client, hdr):
    db = get_database()
    db.set_setting('secondary_provider_enabled', 'true', is_default=False)
    db.set_setting('failover_state:llm:secondary',
                   '{"active": true, "source": "manual", "since": "x", "reason": "r"}',
                   is_default=False)
    with patch('failover.webhook_service.fire_failover_event') as fire:
        r = app_client.put('/api/v1/settings/ad-detection',
                           json={'providerBEnabled': False}, headers=hdr)
    assert r.status_code == 200
    assert db.get_setting('failover_state:llm:secondary') is None
    fire.assert_called_once_with('cancel', 'llm:secondary', 'manual', None)
    event = db.get_failover_events(1)[0]
    assert (event['target'], event['action'], event['source']) == ('llm:secondary', 'cancel', 'manual')
