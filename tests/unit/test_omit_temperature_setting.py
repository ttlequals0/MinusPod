"""Tests for the omit_temperature operator override.

Covers the SETTINGS_REGISTRY entry (registry default + PUT + GET round
trip), and the resolution order in llm_capabilities.model_omits_temperature
/ llm_client's DB-backed override plumbing.
"""
import json

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
