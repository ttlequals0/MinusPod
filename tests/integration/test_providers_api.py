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


def test_put_rejects_userinfo_in_llm_base_url(app_client, temp_db, _auth):
    # An LLM base URL is echoed back by GET and copied into the run's route
    # snapshot, so an embedded credential would leak.
    r = app_client.put(
        '/api/v1/settings/providers/openai',
        json={'baseUrl': 'http://user:pass@server:8000/v1'},
    )
    assert r.status_code == 400
    assert 'credentials' in r.get_json()['error']
    assert temp_db.get_setting('openai_base_url') != 'http://user:pass@server:8000/v1'


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


class TestSecondaryModelListing:
    """GET /settings/models?provider=<type>&slot=secondary: a stage routed to
    the secondary slot needs to discover models with the secondary slot's
    own credentials/base URL, not the primary slot's, even when the
    secondary type differs from the currently configured primary type."""

    def _fake_client_factory(self, calls, models):
        def fake_create_client_for_provider(provider, credential_slot='primary', base_url=None):
            calls.append((provider, credential_slot, base_url))

            class FakeClient:
                def list_models(self):
                    return models
            return FakeClient()
        return fake_create_client_for_provider

    def test_uses_secondary_credential_slot_and_base_url(self, app_client, temp_db, _auth, monkeypatch):
        from llm_client import LLMModel
        temp_db.set_setting('secondary_provider', 'openai-compatible', is_default=False)
        temp_db.set_setting('secondary_provider_base_url', 'http://secondary-host:8000/v1')
        temp_db.set_secret('secondary_provider_api_key', 'sk-secondary')

        calls = []
        monkeypatch.setattr(
            'api.settings.create_client_for_provider',
            self._fake_client_factory(calls, [LLMModel(id='secondary-model-1', name='Secondary Model', created=None)]))

        r = app_client.get('/api/v1/settings/models?provider=openai-compatible&slot=secondary')
        assert r.status_code == 200
        assert [m['id'] for m in r.get_json()['models']] == ['secondary-model-1']
        assert calls == [('openai-compatible', 'secondary', 'http://secondary-host:8000/v1')]

    def test_falls_back_to_default_base_url_when_unset(self, app_client, temp_db, _auth, monkeypatch):
        from config import DEFAULT_OPENAI_BASE_URL
        temp_db.set_setting('secondary_provider', 'ollama', is_default=False)

        calls = []
        monkeypatch.setattr(
            'api.settings.create_client_for_provider', self._fake_client_factory(calls, []))

        r = app_client.get('/api/v1/settings/models?provider=ollama&slot=secondary')
        assert r.status_code == 200
        assert calls == [('ollama', 'secondary', DEFAULT_OPENAI_BASE_URL)]

    def test_primary_slot_omits_secondary_base_url(self, app_client, temp_db, _auth, monkeypatch):
        """slot=primary (the default) must not be routed through the
        secondary-credential path at all."""
        calls = []
        monkeypatch.setattr(
            'api.settings.create_client_for_provider', self._fake_client_factory(calls, []))

        r = app_client.get('/api/v1/settings/models?provider=anthropic')
        assert r.status_code == 200
        assert calls == [('anthropic', 'primary', None)]

    def test_rejects_unknown_slot(self, app_client, temp_db, _auth):
        r = app_client.get('/api/v1/settings/models?provider=anthropic&slot=bogus')
        assert r.status_code == 400
