"""POST /settings/models/refresh: per-slot catalog refresh.

The reviewer's Refresh models button has to reach whichever slot a stage is
routed to, so the endpoint takes an optional {"slot": ...} body. A call with
no body must behave exactly as it did before the slot existed.
"""
import json
from unittest.mock import MagicMock, patch

import pytest

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('settings_models_refresh_test_')

import database
from config import DEFAULT_OPENAI_BASE_URL
from llm_client import LLMModel
from main_app import app

PRIMARY_MODELS = [{'id': 'primary-model', 'name': 'Primary Model', 'created': None}]


@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c


@pytest.fixture
def db():
    handle = database.Database()
    previous = handle.get_setting('secondary_provider')
    yield handle
    if previous:
        handle.set_setting('secondary_provider', previous, is_default=False)
    else:
        handle.clear_setting('secondary_provider')


def _post(client, body=None):
    kwargs = {'content_type': 'application/json'}
    if body is not None:
        kwargs['data'] = json.dumps(body)
    return client.post('/api/v1/settings/models/refresh', **kwargs)


def _primary_detector():
    detector = MagicMock()
    detector.get_available_models.return_value = [dict(m) for m in PRIMARY_MODELS]
    return MagicMock(return_value=detector)


def _secondary_client(calls):
    def fake_get_client_for_provider(provider, base_url=None, credential_slot='primary',
                                     force_new=False):
        calls.append((provider, base_url, credential_slot, force_new))
        fake = MagicMock()
        fake.list_models.return_value = [
            LLMModel(id='secondary-model', name='Secondary Model', created=None)]
        return fake
    return fake_get_client_for_provider


class TestRefreshModelsSlot:
    @pytest.mark.parametrize('body', [None, {'slot': 'primary'}])
    def test_primary_is_the_default_slot(self, client, db, body):
        with patch('api.settings.get_llm_client') as get_primary, \
                patch('api.settings.AdDetector', _primary_detector()):
            response = _post(client, body)
        assert response.status_code == 200, response.data
        payload = response.get_json()
        assert [m['id'] for m in payload['models']] == ['primary-model']
        assert payload['count'] == 1
        get_primary.assert_called_once_with(force_new=True)

    def test_secondary_slot_rebuilds_that_slots_client(self, client, db):
        db.set_setting('secondary_provider', 'ollama', is_default=False)
        calls = []
        with patch('api.settings.get_client_for_provider', _secondary_client(calls)), \
                patch('api.settings.get_llm_client') as get_primary:
            response = _post(client, {'slot': 'secondary'})
        assert response.status_code == 200, response.data
        body = response.get_json()
        assert [m['id'] for m in body['models']] == ['secondary-model']
        assert body['count'] == 1
        assert calls == [('ollama', DEFAULT_OPENAI_BASE_URL, 'secondary', True)]
        get_primary.assert_not_called()

    def test_secondary_slot_without_a_configured_provider_is_rejected(self, client, db):
        db.clear_setting('secondary_provider')
        response = _post(client, {'slot': 'secondary'})
        assert response.status_code == 400
        assert 'secondary' in response.get_json()['error'].lower()

    @pytest.mark.parametrize('slot', ['bogus', '', None, 3])
    def test_unknown_slot_is_rejected(self, client, db, slot):
        response = _post(client, {'slot': slot})
        assert response.status_code == 400
