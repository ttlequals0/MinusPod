"""API tests for the jit_blocked_user_agents global setting.

Mirrors test_community_sync_categories_api.py: covers the top-level _sv
entry in GET /settings and the PUT /settings/ad-detection phase. Each test
patches api.settings.get_database to a fresh temp_db to avoid
order-dependence between tests sharing the same setting key.
"""
import json
from unittest.mock import patch

import pytest

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('jit_blocked_agents_api_test_')
from main_app import app  # noqa: E402


@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c


class TestGetSettingsTopLevelEntry:
    def test_defaults_to_empty_list(self, client, temp_db):
        with patch('api.settings.get_database', return_value=temp_db):
            s = client.get('/api/v1/settings').get_json()
        assert s['jitBlockedUserAgents']['value'] == []
        assert s['jitBlockedUserAgents']['isDefault'] is True
        assert s['defaults']['jitBlockedUserAgents'] == []


class TestAdDetectionPutPhase:
    def _put(self, client, payload):
        return client.put(
            '/api/v1/settings/ad-detection',
            data=json.dumps(payload),
            content_type='application/json',
        )

    def test_put_persists_a_list(self, client, temp_db):
        with patch('api.settings.get_database', return_value=temp_db):
            r = self._put(client, {'jitBlockedUserAgents': ['^atc/', 'WordPress.com']})
            assert r.status_code == 200, r.data
        assert json.loads(temp_db.get_setting('jit_blocked_user_agents')) == [
            '^atc/', 'WordPress.com',
        ]

    def test_put_then_get_roundtrip(self, client, temp_db):
        with patch('api.settings.get_database', return_value=temp_db):
            r = self._put(client, {'jitBlockedUserAgents': ['^atc/']})
            assert r.status_code == 200, r.data
            s = client.get('/api/v1/settings').get_json()
        assert s['jitBlockedUserAgents']['value'] == ['^atc/']
        assert s['jitBlockedUserAgents']['isDefault'] is False

    def test_rejects_non_list(self, client, temp_db):
        with patch('api.settings.get_database', return_value=temp_db):
            r = self._put(client, {'jitBlockedUserAgents': 'not-a-list'})
        assert r.status_code == 400

    def test_drops_blanks_and_rejects_over_length_entry(self, client, temp_db):
        with patch('api.settings.get_database', return_value=temp_db):
            r = self._put(client, {'jitBlockedUserAgents': ['  Bot  ', '   ', '']})
            assert r.status_code == 200, r.data
            s = client.get('/api/v1/settings').get_json()
            assert s['jitBlockedUserAgents']['value'] == ['Bot']

            r2 = self._put(client, {'jitBlockedUserAgents': ['x' * 201]})
        assert r2.status_code == 400
