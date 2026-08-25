"""Tests for the omit_temperature operator override.

Covers the SETTINGS_REGISTRY entry (registry default + PUT + GET round
trip), and the resolution order in llm_capabilities.model_omits_temperature
/ llm_client's DB-backed override plumbing.
"""
import json
from unittest.mock import MagicMock, patch

import pytest

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap(
    'omit_temperature_test_', passphrase='omit-temperature-test-passphrase')

import database  # noqa: E402
import llm_capabilities  # noqa: E402
from main_app import app  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_learned_memo():
    with llm_capabilities._learned_no_temperature_lock:
        llm_capabilities._learned_no_temperature_models.clear()


@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c


def _get_settings(client):
    resp = client.get('/api/v1/settings')
    assert resp.status_code == 200
    return json.loads(resp.data)


class TestRegistryDefault:
    def test_get_exposes_default_of_false(self, client):
        data = _get_settings(client)
        assert data['defaults']['omitTemperature'] is False

    def test_get_top_level_presence(self, client):
        # A prior release shipped a bug where a new key was only in the
        # 'defaults' block, not the top-level {value, isDefault} entry that
        # the settings UI actually reads. Guard against repeating it.
        data = _get_settings(client)
        assert 'omitTemperature' in data
        assert data['omitTemperature'] == {'value': False, 'isDefault': True}


class TestPutValidation:
    def test_put_true_persists(self, client):
        resp = client.put(
            '/api/v1/settings/ad-detection',
            data=json.dumps({'omitTemperature': True}),
            content_type='application/json',
        )
        assert resp.status_code == 200, resp.data
        db = database.Database()
        assert db.get_setting('omit_temperature') == 'true'

    def test_put_false_persists(self, client):
        db = database.Database()
        db.set_setting('omit_temperature', 'true', is_default=False)
        resp = client.put(
            '/api/v1/settings/ad-detection',
            data=json.dumps({'omitTemperature': False}),
            content_type='application/json',
        )
        assert resp.status_code == 200, resp.data
        assert db.get_setting('omit_temperature') == 'false'


class TestGetRoundTrip:
    def test_put_then_get_reflects_override(self, client):
        resp = client.put(
            '/api/v1/settings/ad-detection',
            data=json.dumps({'omitTemperature': True}),
            content_type='application/json',
        )
        assert resp.status_code == 200, resp.data

        data = _get_settings(client)
        assert data['omitTemperature']['value'] is True
        assert data['omitTemperature']['isDefault'] is False


def _make_anthropic_response(text="ok"):
    response = MagicMock()
    response.content = [MagicMock(text=text)]
    response.usage = MagicMock(input_tokens=10, output_tokens=5)
    response.stop_reason = "end_turn"
    return response


class TestModelOmitsTemperatureResolutionOrder:
    """operator override -> static list -> learned memo."""

    def test_override_true_wins_regardless_of_model(self):
        assert llm_capabilities.model_omits_temperature(
            "gpt-4o", operator_override=True) is True

    def test_override_false_falls_through_to_static_list(self):
        assert llm_capabilities.model_omits_temperature(
            "claude-sonnet-5", operator_override=False) is True

    def test_override_false_falls_through_to_learned_memo(self):
        llm_capabilities.mark_model_omits_temperature("claude-unreleased-9")
        assert llm_capabilities.model_omits_temperature(
            "claude-unreleased-9", operator_override=False) is True

    def test_override_false_and_unknown_model_keeps_temperature(self):
        assert llm_capabilities.model_omits_temperature(
            "gpt-4o", operator_override=False) is False


class TestOverrideOmitsTemperatureForAnthropicClient:
    """With the operator override on, temperature is omitted for a model
    that's in neither the static list nor the learned memo."""

    def test_override_true_omits_temperature_for_unlisted_model(self):
        from llm_client import AnthropicClient
        client = AnthropicClient(api_key="dummy")
        mock_sdk = MagicMock()
        mock_sdk.messages.create.return_value = _make_anthropic_response()
        client._client = mock_sdk

        with patch("llm_client._omit_temperature_override", return_value=True):
            client.messages_create(
                model="claude-unlisted-model",
                max_tokens=4096,
                system="sys",
                messages=[{"role": "user", "content": "hi"}],
                temperature=0.2,
            )

        kwargs = mock_sdk.messages.create.call_args.kwargs
        assert "extra_body" not in kwargs

    def test_override_false_keeps_existing_behavior_for_unlisted_model(self):
        from llm_client import AnthropicClient
        client = AnthropicClient(api_key="dummy")
        mock_sdk = MagicMock()
        mock_sdk.messages.create.return_value = _make_anthropic_response()
        client._client = mock_sdk

        with patch("llm_client._omit_temperature_override", return_value=False):
            client.messages_create(
                model="claude-unlisted-model",
                max_tokens=4096,
                system="sys",
                messages=[{"role": "user", "content": "hi"}],
                temperature=0.2,
            )

        kwargs = mock_sdk.messages.create.call_args.kwargs
        assert kwargs["extra_body"] == {"temperature": 0.2}

    def test_override_false_static_list_still_omits_temperature(self):
        from llm_client import AnthropicClient
        client = AnthropicClient(api_key="dummy")
        mock_sdk = MagicMock()
        mock_sdk.messages.create.return_value = _make_anthropic_response()
        client._client = mock_sdk

        with patch("llm_client._omit_temperature_override", return_value=False):
            client.messages_create(
                model="claude-sonnet-5",
                max_tokens=4096,
                system="sys",
                messages=[{"role": "user", "content": "hi"}],
                temperature=0.0,
            )

        kwargs = mock_sdk.messages.create.call_args.kwargs
        assert "extra_body" not in kwargs

    def test_override_false_learned_memo_still_omits_temperature(self):
        from llm_client import AnthropicClient
        llm_capabilities.mark_model_omits_temperature("claude-unlisted-model")
        client = AnthropicClient(api_key="dummy")
        mock_sdk = MagicMock()
        mock_sdk.messages.create.return_value = _make_anthropic_response()
        client._client = mock_sdk

        with patch("llm_client._omit_temperature_override", return_value=False):
            client.messages_create(
                model="claude-unlisted-model",
                max_tokens=4096,
                system="sys",
                messages=[{"role": "user", "content": "hi"}],
                temperature=0.3,
            )

        kwargs = mock_sdk.messages.create.call_args.kwargs
        assert "extra_body" not in kwargs


class TestOmitTemperatureOverrideReadsSetting:
    def test_reads_true_from_db(self):
        from llm_client import _clear_provider_cache, _omit_temperature_override
        db = database.Database()
        db.set_setting('omit_temperature', 'true', is_default=False)
        _clear_provider_cache()
        try:
            assert _omit_temperature_override() is True
        finally:
            db.set_setting('omit_temperature', 'false', is_default=True)
            _clear_provider_cache()

    def test_reads_false_by_default(self):
        from llm_client import _clear_provider_cache, _omit_temperature_override
        db = database.Database()
        db.set_setting('omit_temperature', 'false', is_default=True)
        _clear_provider_cache()
        assert _omit_temperature_override() is False
