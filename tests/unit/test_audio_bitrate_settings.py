"""Unit tests for audio output bitrate settings round-trip.

Covers the bug where audioBitrate was wired in the frontend but dropped by the
backend: GET /settings omitted it and PUT /settings/ad-detection had no phase to
persist it. See plan: fix audio-bitrate save bug + normalize env flow.
"""
import os
import sys
import tempfile
import json

import pytest

# Create temp data dir and set env before any imports that touch /app/data
_test_data_dir = tempfile.mkdtemp(prefix='audio_bitrate_test_')
os.environ['SECRET_KEY'] = 'test-secret'
os.environ['DATA_DIR'] = _test_data_dir
os.environ['MINUSPOD_MASTER_PASSPHRASE'] = 'audio-bitrate-test-passphrase'

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

import database
import storage as storage_mod
database.Database._instance = None
database.Database.__init__.__defaults__ = (_test_data_dir,)
database.Database.__new__.__defaults__ = (_test_data_dir,)
storage_mod.Storage.__init__.__defaults__ = (_test_data_dir,)

from main_app import app


@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c


def _get_settings(client):
    resp = client.get('/api/v1/settings')
    assert resp.status_code == 200
    return json.loads(resp.data)


def test_get_includes_audio_bitrate_default(client):
    data = _get_settings(client)
    assert 'audioBitrate' in data, "GET /settings must expose audioBitrate"
    assert data['audioBitrate']['value'] == '128k'
    assert data['audioBitrate']['isDefault'] is True
    assert data['defaults']['audioBitrate'] == '128k'


def test_put_persists_valid_bitrate(client):
    resp = client.put(
        '/api/v1/settings/ad-detection',
        data=json.dumps({'audioBitrate': '256k'}),
        content_type='application/json',
    )
    assert resp.status_code == 200

    data = _get_settings(client)
    assert data['audioBitrate']['value'] == '256k'
    assert data['audioBitrate']['isDefault'] is False


def test_put_rejects_invalid_bitrate(client):
    resp = client.put(
        '/api/v1/settings/ad-detection',
        data=json.dumps({'audioBitrate': '999k'}),
        content_type='application/json',
    )
    assert resp.status_code == 400
    err = json.loads(resp.data)['error']
    assert 'audioBitrate' in err


def test_reset_restores_default_bitrate(client):
    client.put(
        '/api/v1/settings/ad-detection',
        data=json.dumps({'audioBitrate': '64k'}),
        content_type='application/json',
    )
    assert _get_settings(client)['audioBitrate']['value'] == '64k'

    resp = client.post('/api/v1/settings/ad-detection/reset')
    assert resp.status_code == 200

    data = _get_settings(client)
    assert data['audioBitrate']['value'] == '128k'
    assert data['audioBitrate']['isDefault'] is True
