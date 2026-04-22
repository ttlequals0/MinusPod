"""Unit tests for settings API validation (OpenRouter key format)."""
import os
import sys
import tempfile
import json
from unittest.mock import patch, MagicMock

import pytest

# Create temp data dir and set env before any imports that touch /app/data
_test_data_dir = tempfile.mkdtemp(prefix='settings_test_')
os.environ['SECRET_KEY'] = 'test-secret'
os.environ['DATA_DIR'] = _test_data_dir
os.environ['MINUSPOD_MASTER_PASSPHRASE'] = 'settings-validation-test-passphrase'

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
    """Flask test client."""
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c


class TestOpenRouterKeyValidation:
    """Tests for OpenRouter API key format validation in settings endpoint."""

    def test_rejects_key_without_sk_or_prefix(self, client):
        response = client.put(
            '/api/v1/settings/ad-detection',
            data=json.dumps({'openrouterApiKey': 'sk-ant-wrong-prefix'}),
            content_type='application/json',
        )
        assert response.status_code == 400
        data = json.loads(response.data)
        assert 'sk-or-' in data['error']

    def test_accepts_valid_sk_or_key(self, client):
        response = client.put(
            '/api/v1/settings/ad-detection',
            data=json.dumps({'openrouterApiKey': 'sk-or-v1-valid-key'}),
            content_type='application/json',
        )
        assert response.status_code == 200

    def test_accepts_empty_key_for_reset(self, client):
        response = client.put(
            '/api/v1/settings/ad-detection',
            data=json.dumps({'openrouterApiKey': ''}),
            content_type='application/json',
        )
        assert response.status_code == 200

    def test_strips_whitespace_before_validation(self, client):
        response = client.put(
            '/api/v1/settings/ad-detection',
            data=json.dumps({'openrouterApiKey': '  sk-or-v1-padded  '}),
            content_type='application/json',
        )
        assert response.status_code == 200


class TestResetSettingSecretKeys:
    """reset_setting on a SECRET_SETTING_KEYS entry must DELETE the row,
    not write empty string. Empty-string rows surface as "configured"
    elsewhere and trip the plaintext-secret read warning."""

    def test_reset_deletes_secret_row(self):
        db = database.Database()
        db.set_secret('openrouter_api_key', 'sk-or-test-value')
        assert db.get_setting('openrouter_api_key') is not None

        assert db.reset_setting('openrouter_api_key') is True
        # Row is deleted, not blank.
        assert db.get_setting('openrouter_api_key') is None

    def test_reset_non_secret_writes_default(self):
        db = database.Database()
        db.set_setting('whisper_model', 'large-v3', is_default=False)
        assert db.reset_setting('whisper_model') is True
        assert db.get_setting('whisper_model') is not None


class TestWebhookUrlValidation:
    """Issue #158: webhooks must accept private-IP / non-default-port URLs
    (the OPERATOR_CONFIGURED trust posture used by LLM and Whisper base URLs)
    while still blocking cloud metadata IPs and bad schemes.
    """

    def test_create_webhook_allows_private_ip_url(self, client):
        response = client.post(
            '/api/v1/settings/webhooks',
            data=json.dumps({
                'url': 'http://192.168.1.10:8123/api/webhook/abc',
                'events': ['Episode Processed'],
            }),
            content_type='application/json',
        )
        assert response.status_code == 201, response.data

    def test_create_webhook_blocks_metadata_ip(self, client):
        response = client.post(
            '/api/v1/settings/webhooks',
            data=json.dumps({
                'url': 'http://169.254.169.254/latest/meta-data/',
                'events': ['Episode Processed'],
            }),
            content_type='application/json',
        )
        assert response.status_code == 400
        data = json.loads(response.data)
        assert 'metadata' in data['error'].lower()

    def test_create_webhook_blocks_bad_scheme(self, client):
        response = client.post(
            '/api/v1/settings/webhooks',
            data=json.dumps({
                'url': 'ftp://hook.example.com/path',
                'events': ['Episode Processed'],
            }),
            content_type='application/json',
        )
        assert response.status_code == 400
