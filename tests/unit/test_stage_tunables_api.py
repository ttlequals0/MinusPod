"""API validation tests for per-stage LLM tunables in PUT /settings/ad-detection."""
import json
import os
import sys
import tempfile
from unittest.mock import patch

import pytest

_test_data_dir = tempfile.mkdtemp(prefix='tunables_api_test_')
os.environ['SECRET_KEY'] = 'test-secret'
os.environ['DATA_DIR'] = _test_data_dir
os.environ['MINUSPOD_MASTER_PASSPHRASE'] = 'tunables-api-test-passphrase'

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


def _put(client, payload):
    return client.put(
        '/api/v1/settings/ad-detection',
        data=json.dumps(payload),
        content_type='application/json',
    )


class TestTemperatureValidation:
    def test_valid_temperature_accepted(self, client):
        r = _put(client, {'detectionTemperature': 0.5})
        assert r.status_code == 200, r.data

    def test_temperature_above_max_rejected(self, client):
        r = _put(client, {'detectionTemperature': 2.5})
        assert r.status_code == 400
        assert 'detectionTemperature' in json.loads(r.data)['error']

    def test_temperature_non_numeric_rejected(self, client):
        r = _put(client, {'detectionTemperature': 'hot'})
        assert r.status_code == 400


class TestMaxTokensValidation:
    def test_valid_max_tokens(self, client):
        r = _put(client, {'reviewerMaxTokens': 4096})
        assert r.status_code == 200

    def test_below_min_rejected(self, client):
        r = _put(client, {'reviewerMaxTokens': 64})
        assert r.status_code == 400

    def test_above_max_rejected(self, client):
        r = _put(client, {'reviewerMaxTokens': 999999})
        assert r.status_code == 400


class TestReasoningBudget:
    def test_anthropic_budget_accepted(self, client):
        # Force provider to anthropic via DB before validation.
        from database import Database
        Database().set_setting('llm_provider', 'anthropic', is_default=False)
        r = _put(client, {'detectionReasoningBudget': 8192})
        assert r.status_code == 200, r.data

    def test_budget_rejected_when_provider_not_anthropic(self, client):
        from database import Database
        Database().set_setting('llm_provider', 'openrouter', is_default=False)
        r = _put(client, {'detectionReasoningBudget': 8192})
        assert r.status_code == 400
        assert 'anthropic' in json.loads(r.data)['error']

    def test_inline_provider_change_to_anthropic_allows_budget(self, client):
        from database import Database
        Database().set_setting('llm_provider', 'openrouter', is_default=False)
        r = _put(client, {
            'llmProvider': 'anthropic',
            'detectionReasoningBudget': 8192,
        })
        assert r.status_code == 200

    def test_out_of_range_rejected(self, client):
        from database import Database
        Database().set_setting('llm_provider', 'anthropic', is_default=False)
        r = _put(client, {'detectionReasoningBudget': 100})
        assert r.status_code == 400


class TestReasoningLevel:
    def test_level_rejected_when_provider_anthropic(self, client):
        from database import Database
        Database().set_setting('llm_provider', 'anthropic', is_default=False)
        r = _put(client, {'detectionReasoningLevel': 'high'})
        assert r.status_code == 400

    def test_level_accepted_for_openrouter(self, client):
        from database import Database
        Database().set_setting('llm_provider', 'openrouter', is_default=False)
        r = _put(client, {'detectionReasoningLevel': 'medium'})
        assert r.status_code == 200

    def test_invalid_level_rejected(self, client):
        from database import Database
        Database().set_setting('llm_provider', 'openrouter', is_default=False)
        r = _put(client, {'detectionReasoningLevel': 'extreme'})
        assert r.status_code == 400


class TestOllamaNumCtx:
    def test_rejected_when_provider_not_ollama(self, client):
        from database import Database
        Database().set_setting('llm_provider', 'anthropic', is_default=False)
        r = _put(client, {'ollamaNumCtx': 8192})
        assert r.status_code == 400

    def test_accepted_when_provider_ollama(self, client):
        from database import Database
        Database().set_setting('llm_provider', 'ollama', is_default=False)
        r = _put(client, {'ollamaNumCtx': 8192})
        assert r.status_code == 200, r.data

    def test_out_of_range_rejected(self, client):
        from database import Database
        Database().set_setting('llm_provider', 'ollama', is_default=False)
        r = _put(client, {'ollamaNumCtx': 100})
        assert r.status_code == 400


class TestNullClears:
    def test_null_temperature_clears(self, client):
        from database import Database
        db = Database()
        db.set_setting('detection_temperature', '0.5', is_default=False)
        r = _put(client, {'detectionTemperature': None})
        assert r.status_code == 200
        # After clear, stored value is empty string (treated as default by reader).
        assert db.get_setting('detection_temperature') == ''
