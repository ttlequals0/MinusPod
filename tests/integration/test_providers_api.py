"""Integration tests for /api/v1/settings/providers.

Skipped outside the Docker container (same gate as other integration tests).
"""
import os
import sys

import pytest

pytest.importorskip("ctranslate2", reason="Integration tests require Docker environment")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

import secrets_crypto  # noqa: E402


@pytest.fixture(autouse=True)
def _crypto(monkeypatch):
    monkeypatch.setenv('MINUSPOD_MASTER_PASSPHRASE', 'test-pass')
    secrets_crypto.reset_cache()
    yield
    secrets_crypto.reset_cache()


@pytest.fixture
def _auth(monkeypatch):
    # Bypass the @api.before_request auth gate by setting ADMIN_PASSWORD empty.
    monkeypatch.delenv('ADMIN_PASSWORD', raising=False)
    yield


def test_get_never_returns_key_values(app_client, temp_db, _auth):
    temp_db.set_secret('anthropic_api_key', 'sk-ant-abc')
    r = app_client.get('/api/v1/settings/providers')
    assert r.status_code == 200
    data = r.get_json()
    # Scan every string in the payload: no substring of the secret allowed.
    import json
    blob = json.dumps(data)
    assert 'sk-ant-abc' not in blob
    assert data['anthropic']['configured'] is True
    assert data['anthropic']['source'] == 'db'


def test_put_stores_encrypted(app_client, temp_db, _auth):
    r = app_client.put(
        '/api/v1/settings/providers/anthropic',
        json={'apiKey': 'sk-ant-xyz'},
    )
    assert r.status_code == 200
    raw = temp_db.get_setting('anthropic_api_key')
    assert raw.startswith('enc:v1:')
    assert 'sk-ant-xyz' not in raw
    assert temp_db.get_secret('anthropic_api_key') == 'sk-ant-xyz'


def test_put_unknown_provider(app_client, temp_db, _auth):
    r = app_client.put('/api/v1/settings/providers/bogus', json={'apiKey': 'x'})
    assert r.status_code == 404


def test_delete_clears(app_client, temp_db, _auth):
    temp_db.set_secret('openrouter_api_key', 'sk-or-abc')
    r = app_client.delete('/api/v1/settings/providers/openrouter')
    assert r.status_code == 200
    assert temp_db.get_secret('openrouter_api_key') is None


def test_put_rejects_bad_base_url(app_client, temp_db, _auth):
    r = app_client.put(
        '/api/v1/settings/providers/whisper',
        json={'baseUrl': 'http://169.254.169.254/latest'},
    )
    assert r.status_code == 400


def test_locked_when_crypto_unavailable(app_client, temp_db, monkeypatch, _auth):
    monkeypatch.delenv('MINUSPOD_MASTER_PASSPHRASE', raising=False)
    secrets_crypto.reset_cache()
    r = app_client.get('/api/v1/settings/providers')
    assert r.status_code == 200
    assert r.get_json()['cryptoReady'] is False
    r2 = app_client.put(
        '/api/v1/settings/providers/anthropic',
        json={'apiKey': 'sk-x'},
    )
    assert r2.status_code == 409
