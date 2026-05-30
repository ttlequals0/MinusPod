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


class TestReviewerPromptReset:
    """Issue #301: the Ad Reviewer review/resurrect prompts were missing from
    reset_setting's defaults dict, so the reset button was a silent no-op and a
    cleared-then-saved prompt stayed blank forever. These lock in that reset
    works for all four prompts and that a blank save reverts to default."""

    def test_reset_setting_restores_reviewer_prompt_defaults(self):
        db = database.Database()
        db.set_setting('review_prompt', 'custom review', is_default=False)
        db.set_setting('resurrect_prompt', 'custom resurrect', is_default=False)

        assert db.reset_setting('review_prompt') is True
        assert db.reset_setting('resurrect_prompt') is True
        assert db.get_setting('review_prompt') == database.DEFAULT_REVIEW_PROMPT
        assert db.get_setting('resurrect_prompt') == database.DEFAULT_RESURRECT_PROMPT

    def test_prompts_reset_endpoint_restores_reviewer_prompts(self, client):
        db = database.Database()
        db.set_setting('review_prompt', 'custom review', is_default=False)
        db.set_setting('resurrect_prompt', 'custom resurrect', is_default=False)

        response = client.post('/api/v1/settings/prompts/reset')
        assert response.status_code == 200, response.data

        data = json.loads(client.get('/api/v1/settings').data)
        assert data['reviewPrompt']['value'] == database.DEFAULT_REVIEW_PROMPT
        assert data['reviewPrompt']['isDefault'] is True
        assert data['resurrectPrompt']['value'] == database.DEFAULT_RESURRECT_PROMPT
        assert data['resurrectPrompt']['isDefault'] is True

    def test_blank_reviewer_prompt_save_reverts_to_default(self, client):
        db = database.Database()
        db.set_setting('review_prompt', 'custom review', is_default=False)

        response = client.put(
            '/api/v1/settings/ad-detection',
            data=json.dumps({'reviewPrompt': '', 'resurrectPrompt': '   '}),
            content_type='application/json',
        )
        assert response.status_code == 200, response.data

        data = json.loads(client.get('/api/v1/settings').data)
        assert data['reviewPrompt']['value'] == database.DEFAULT_REVIEW_PROMPT
        assert data['reviewPrompt']['isDefault'] is True
        assert data['resurrectPrompt']['value'] == database.DEFAULT_RESURRECT_PROMPT
        assert data['resurrectPrompt']['isDefault'] is True

    def test_non_string_prompt_payload_does_not_500(self, client):
        # A wrong-typed value must not crash the blank-check .strip() (regression).
        response = client.put(
            '/api/v1/settings/ad-detection',
            data=json.dumps({'reviewPrompt': 5}),
            content_type='application/json',
        )
        assert response.status_code != 500, response.data


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


class TestPartialUpdatePreservesOtherFields:
    """A PUT to /settings/ad-detection with one field must not touch the
    others. Locks in the `if 'fieldName' in data:` guard pattern that the
    frontend's diff-only payload depends on."""

    def test_single_field_save_leaves_others_untouched(self, client):
        db = database.Database()
        db.set_setting('whisper_backend', 'api', is_default=False)
        db.set_setting('whisper_api_base_url', 'https://my-whisper.example.com', is_default=False)
        db.set_setting('whisper_api_model', 'large-v3', is_default=False)
        db.set_setting('llm_provider', 'openrouter', is_default=False)
        db.set_setting('claude_model', 'anthropic/claude-sonnet-4.6', is_default=False)

        response = client.put(
            '/api/v1/settings/ad-detection',
            data=json.dumps({'chaptersEnabled': False}),
            content_type='application/json',
        )
        assert response.status_code == 200, response.data

        assert db.get_setting('whisper_backend') == 'api'
        assert db.get_setting('whisper_api_base_url') == 'https://my-whisper.example.com'
        assert db.get_setting('whisper_api_model') == 'large-v3'
        assert db.get_setting('llm_provider') == 'openrouter'
        assert db.get_setting('claude_model') == 'anthropic/claude-sonnet-4.6'
        assert db.get_setting('chapters_enabled') == 'false'

    def test_revert_to_defaults_is_accepted(self, client):
        db = database.Database()
        db.set_setting('whisper_backend', 'api', is_default=False)
        db.set_setting('whisper_api_base_url', 'https://my-whisper.example.com', is_default=False)
        db.set_setting('whisper_api_model', 'large-v3', is_default=False)

        response = client.put(
            '/api/v1/settings/ad-detection',
            data=json.dumps({
                'whisperBackend': 'local',
                'whisperApiBaseUrl': '',
                'whisperApiModel': 'whisper-1',
            }),
            content_type='application/json',
        )
        assert response.status_code == 200, response.data
        assert db.get_setting('whisper_backend') == 'local'
        assert db.get_setting('whisper_api_base_url') == ''
        assert db.get_setting('whisper_api_model') == 'whisper-1'


class TestProviderChangeModelPruning:
    """Provider-change pruning at _apply_provider_fields must NOT reset saved
    model IDs when the new provider's catalog probe came back empty -- that
    means the lookup failed (bad key, network 5xx, unreachable), not that
    every prior model is invalid. Issue #266: changing the provider with a
    misconfigured key was wiping claude_model / verification_model /
    chapters_model on every save."""

    def test_empty_catalog_preserves_existing_model_selections(self, client):
        db = database.Database()
        db.set_setting('llm_provider', 'anthropic', is_default=False)
        db.set_setting('claude_model', 'claude-sonnet-4-5-20250929', is_default=False)
        db.set_setting('verification_model', 'claude-sonnet-4-5-20250929', is_default=False)
        db.set_setting('chapters_model', 'claude-haiku-4-5-20251001', is_default=False)

        # Force list_models() to return [] regardless of the provider being
        # switched to; mirrors what the OpenAI SDK does on 401 (logs + empty).
        fake_client = MagicMock()
        fake_client.list_models.return_value = []
        fake_client.probe_json_format_support.return_value = None
        with patch('api.settings.get_llm_client', return_value=fake_client):
            response = client.put(
                '/api/v1/settings/ad-detection',
                data=json.dumps({'llmProvider': 'openai-compatible'}),
                content_type='application/json',
            )
        assert response.status_code == 200, response.data
        assert db.get_setting('llm_provider') == 'openai-compatible'
        assert db.get_setting('claude_model') == 'claude-sonnet-4-5-20250929'
        assert db.get_setting('verification_model') == 'claude-sonnet-4-5-20250929'
        assert db.get_setting('chapters_model') == 'claude-haiku-4-5-20251001'

    def test_list_models_raises_preserves_existing_model_selections(self, client):
        db = database.Database()
        db.set_setting('llm_provider', 'anthropic', is_default=False)
        db.set_setting('claude_model', 'claude-sonnet-4-5-20250929', is_default=False)

        fake_client = MagicMock()
        fake_client.list_models.side_effect = RuntimeError('network down')
        fake_client.probe_json_format_support.return_value = None
        with patch('api.settings.get_llm_client', return_value=fake_client):
            response = client.put(
                '/api/v1/settings/ad-detection',
                data=json.dumps({'llmProvider': 'openai-compatible'}),
                content_type='application/json',
            )
        assert response.status_code == 200, response.data
        assert db.get_setting('claude_model') == 'claude-sonnet-4-5-20250929'

    def test_populated_catalog_still_prunes_stale_selections(self, client):
        """The prune is the original feature -- a model not in the new
        provider's catalog still gets reset. Regression guard so the empty-
        list fix above does not over-correct into never-pruning."""
        db = database.Database()
        db.set_setting('llm_provider', 'anthropic', is_default=False)
        db.set_setting('claude_model', 'openai/gpt-stale', is_default=False)
        db.set_setting('chapters_model', 'claude-haiku-4-5-20251001', is_default=False)

        fake_model_a = MagicMock(id='claude-haiku-4-5-20251001')
        fake_model_b = MagicMock(id='claude-sonnet-4-5-20250929')
        fake_client = MagicMock()
        fake_client.list_models.return_value = [fake_model_a, fake_model_b]
        fake_client.probe_json_format_support.return_value = None
        with patch('api.settings.get_llm_client', return_value=fake_client):
            response = client.put(
                '/api/v1/settings/ad-detection',
                data=json.dumps({'llmProvider': 'anthropic'}),
                content_type='application/json',
            )
        assert response.status_code == 200, response.data
        assert db.get_setting('claude_model') == 'claude-sonnet-4-5-20250929'
        assert db.get_setting('chapters_model') == 'claude-haiku-4-5-20251001'


class TestAudioBitrateValidation:
    """audioBitrate round-trip + validation.

    Regression: GET /settings omitted audioBitrate and PUT had no apply phase,
    so the frontend selector silently did nothing.
    """

    def _get_settings(self, client):
        resp = client.get('/api/v1/settings')
        assert resp.status_code == 200
        return json.loads(resp.data)

    def test_get_exposes_audio_bitrate_with_default(self, client):
        data = self._get_settings(client)
        assert 'audioBitrate' in data
        assert data['audioBitrate']['value'] == '128k'
        assert data['audioBitrate']['isDefault'] is True
        assert data['defaults']['audioBitrate'] == '128k'

    def test_put_persists_valid_bitrate(self, client):
        resp = client.put(
            '/api/v1/settings/ad-detection',
            data=json.dumps({'audioBitrate': '256k'}),
            content_type='application/json',
        )
        assert resp.status_code == 200

        data = self._get_settings(client)
        assert data['audioBitrate']['value'] == '256k'
        assert data['audioBitrate']['isDefault'] is False

    def test_put_rejects_invalid_bitrate(self, client):
        resp = client.put(
            '/api/v1/settings/ad-detection',
            data=json.dumps({'audioBitrate': '999k'}),
            content_type='application/json',
        )
        assert resp.status_code == 400
        assert 'audioBitrate' in json.loads(resp.data)['error']

    def test_reset_restores_default_bitrate(self, client):
        client.put(
            '/api/v1/settings/ad-detection',
            data=json.dumps({'audioBitrate': '64k'}),
            content_type='application/json',
        )
        assert self._get_settings(client)['audioBitrate']['value'] == '64k'

        resp = client.post('/api/v1/settings/ad-detection/reset')
        assert resp.status_code == 200

        data = self._get_settings(client)
        assert data['audioBitrate']['value'] == '128k'
        assert data['audioBitrate']['isDefault'] is True


class TestSkipFlacCompressionValidation:
    """skipFlacCompression boolean round-trip + reset.

    The toggle lives in `_apply_whisper_fields` and only matters when the
    Whisper API backend is selected, but the setting itself is global so it
    is exercised here regardless of backend.
    """

    def _get_settings(self, client):
        resp = client.get('/api/v1/settings')
        assert resp.status_code == 200
        return json.loads(resp.data)

    def test_get_exposes_skip_flac_with_default_false(self, client):
        data = self._get_settings(client)
        assert 'skipFlacCompression' in data
        assert data['skipFlacCompression']['value'] is False
        assert data['skipFlacCompression']['isDefault'] is True
        assert data['defaults']['skipFlacCompression'] is False

    def test_put_persists_true_as_bool(self, client):
        resp = client.put(
            '/api/v1/settings/ad-detection',
            data=json.dumps({'skipFlacCompression': True}),
            content_type='application/json',
        )
        assert resp.status_code == 200

        data = self._get_settings(client)
        assert data['skipFlacCompression']['value'] is True
        assert data['skipFlacCompression']['isDefault'] is False

    def test_put_accepts_truthy_strings(self, client):
        for raw in ('true', '1', 'yes', 'TRUE', 'Yes'):
            resp = client.put(
                '/api/v1/settings/ad-detection',
                data=json.dumps({'skipFlacCompression': raw}),
                content_type='application/json',
            )
            assert resp.status_code == 200, raw
            assert self._get_settings(client)['skipFlacCompression']['value'] is True

    def test_put_accepts_falsy_strings(self, client):
        # First flip it on so we can confirm the falsy path actually turns it off.
        client.put(
            '/api/v1/settings/ad-detection',
            data=json.dumps({'skipFlacCompression': True}),
            content_type='application/json',
        )
        for raw in ('false', '0', 'no', 'FALSE', 'No'):
            resp = client.put(
                '/api/v1/settings/ad-detection',
                data=json.dumps({'skipFlacCompression': True}),
                content_type='application/json',
            )
            assert resp.status_code == 200

            resp = client.put(
                '/api/v1/settings/ad-detection',
                data=json.dumps({'skipFlacCompression': raw}),
                content_type='application/json',
            )
            assert resp.status_code == 200, raw
            assert self._get_settings(client)['skipFlacCompression']['value'] is False

    def test_reset_restores_false(self, client):
        client.put(
            '/api/v1/settings/ad-detection',
            data=json.dumps({'skipFlacCompression': True}),
            content_type='application/json',
        )
        assert self._get_settings(client)['skipFlacCompression']['value'] is True

        resp = client.post('/api/v1/settings/ad-detection/reset')
        assert resp.status_code == 200

        data = self._get_settings(client)
        assert data['skipFlacCompression']['value'] is False
        assert data['skipFlacCompression']['isDefault'] is True


class TestAdDetectionParallelWindowsValidation:
    """adDetectionParallelWindows int round-trip, range validator, and reset."""

    def _get_settings(self, client):
        resp = client.get('/api/v1/settings')
        assert resp.status_code == 200
        return json.loads(resp.data)

    def test_get_exposes_default_of_4(self, client):
        data = self._get_settings(client)
        assert 'adDetectionParallelWindows' in data
        assert data['adDetectionParallelWindows']['value'] == 4
        assert data['adDetectionParallelWindows']['isDefault'] is True
        assert data['defaults']['adDetectionParallelWindows'] == 4

    def test_put_persists_valid_int(self, client):
        resp = client.put(
            '/api/v1/settings/ad-detection',
            data=json.dumps({'adDetectionParallelWindows': 8}),
            content_type='application/json',
        )
        assert resp.status_code == 200

        data = self._get_settings(client)
        assert data['adDetectionParallelWindows']['value'] == 8
        assert data['adDetectionParallelWindows']['isDefault'] is False

    def test_put_rejects_zero(self, client):
        resp = client.put(
            '/api/v1/settings/ad-detection',
            data=json.dumps({'adDetectionParallelWindows': 0}),
            content_type='application/json',
        )
        assert resp.status_code == 400
        assert 'adDetectionParallelWindows' in json.loads(resp.data)['error']

    def test_put_rejects_over_ceiling(self, client):
        resp = client.put(
            '/api/v1/settings/ad-detection',
            data=json.dumps({'adDetectionParallelWindows': 33}),
            content_type='application/json',
        )
        assert resp.status_code == 400

    def test_put_rejects_non_integer(self, client):
        resp = client.put(
            '/api/v1/settings/ad-detection',
            data=json.dumps({'adDetectionParallelWindows': 'four'}),
            content_type='application/json',
        )
        assert resp.status_code == 400

    def test_reset_restores_default(self, client):
        client.put(
            '/api/v1/settings/ad-detection',
            data=json.dumps({'adDetectionParallelWindows': 16}),
            content_type='application/json',
        )
        assert self._get_settings(client)['adDetectionParallelWindows']['value'] == 16

        resp = client.post('/api/v1/settings/ad-detection/reset')
        assert resp.status_code == 200

        data = self._get_settings(client)
        assert data['adDetectionParallelWindows']['value'] == 4
        assert data['adDetectionParallelWindows']['isDefault'] is True
