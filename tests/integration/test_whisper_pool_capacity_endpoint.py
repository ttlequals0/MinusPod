"""GET /settings/whisper/capacity reports the resolved pool."""
import os
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='whisper-capacity-'))


def _csrf(app_client):
    with app_client.session_transaction() as sess:
        sess['authenticated'] = True
    app_client.get('/api/v1/auth/status')
    cookie = app_client.get_cookie('minuspod_csrf')
    return {'X-CSRF-Token': cookie.value} if cookie else {}


def test_capacity_reports_disabled_by_default(app_client):
    _csrf(app_client)
    body = app_client.get('/api/v1/settings/whisper/capacity').get_json()
    assert body['enabled'] is False
    assert body['active'] is False
    assert body['inactiveReason'] == 'disabled'
    assert body['chunkWorkers'] == {'configured': 4, 'effective': 4}
    assert body['worstCaseInFlight'] == 4
    assert body['exceedsCapacity'] is False
    assert isinstance(body['leader'], bool)
    assert body['health'] == {'available': False}


def test_capacity_reflects_settings_and_local_backend(app_client):
    hdr = _csrf(app_client)
    try:
        app_client.put('/api/v1/settings/ad-detection', json={
            'whisperBackend': 'openai-api', 'whisperApiBaseUrl': 'https://whisper.example.com/v1',
            'whisperPoolEnabled': True, 'whisperPoolMaxRequests': 3, 'whisperPoolMaxEpisodes': 2,
            'transcribeConcurrentChunks': 4,
        }, headers=hdr)
        # No real health endpoint behind this placeholder host: patch the
        # probe so an active pool never triggers outbound network I/O here.
        with patch('transcriber.probe_whisper_health',
                   return_value={'available': False}) as health_probe:
            body = app_client.get('/api/v1/settings/whisper/capacity').get_json()
        assert health_probe.called
        assert body['active'] is True and body['capacity'] == 3
        assert body['maxEpisodes'] == {'configured': 2, 'effective': 2}
        assert body['chunkWorkers'] == {'configured': 4, 'effective': 3}
        assert body['worstCaseInFlight'] == 8 and body['exceedsCapacity'] is True
        assert body['health'] == {'available': False}
        app_client.put('/api/v1/settings/ad-detection', json={'whisperBackend': 'local'}, headers=hdr)
        with patch('transcriber.probe_whisper_health') as health_probe:
            body = app_client.get('/api/v1/settings/whisper/capacity').get_json()
        assert not health_probe.called
        assert body['active'] is False and body['inactiveReason'] == 'local_backend'
        assert body['maxEpisodes']['effective'] == 1
    finally:
        # Restore defaults: this suite shares a DB with sibling test modules.
        app_client.put('/api/v1/settings/ad-detection', json={
            'whisperPoolEnabled': False, 'whisperPoolMaxRequests': 4, 'whisperPoolMaxEpisodes': 1,
        }, headers=hdr)
