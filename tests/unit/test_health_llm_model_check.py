"""Tests for /health surfacing whether a detection model is configured (#4)."""
from unittest.mock import MagicMock, patch

import pytest

from tests.app_bootstrap import bootstrap

bootstrap('health_llm_model_test_')

from main_app import app  # noqa: E402


@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c


def _get_health(client, db, storage_dir):
    # Route the handler at a scoped temp_db/storage instead of the ambient
    # singleton, which other test modules in the same session may repoint.
    storage = MagicMock(data_dir=str(storage_dir))
    with patch('api.system.get_database', return_value=db), \
         patch('api.system.get_storage', return_value=storage):
        return client.get('/api/v1/health')


class TestHealthLLMModelConfigured:
    def test_reports_true_when_model_configured(self, client, temp_db, temp_dir):
        temp_db.set_setting('claude_model', 'claude-sonnet-5')
        response = _get_health(client, temp_db, temp_dir)
        assert response.status_code == 200
        data = response.get_json()
        assert data['checks']['llm_model_configured'] is True
        assert data['status'] == 'healthy'

    def test_reports_false_when_model_unconfigured_without_flipping_status(
            self, client, temp_db, temp_dir):
        temp_db.clear_setting('claude_model')
        response = _get_health(client, temp_db, temp_dir)
        assert response.status_code == 200
        data = response.get_json()
        assert data['checks']['llm_model_configured'] is False
        assert data['status'] == 'healthy'  # database/storage still fine
