"""Tests for the LLM provider and PodcastIndex connection tests (#544).

The whisper transcriber probe is covered in test_whisper_connection_test.py;
this file covers the /models probe behind
/settings/providers/{openai,ollama}/test-connection and the
/settings/podcast-index/test endpoint.
"""
import hashlib
import json
from unittest.mock import patch, MagicMock

import pytest
import requests as requests_lib

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('conn_probes_test_', passphrase='conn-probes-test-pass')

from main_app import app  # noqa: E402
from api.providers import _probe_models_endpoint, _same_server  # noqa: E402
from api.podcast_search import _podcast_index_headers  # noqa: E402
from llm_client import invalidate_provider_cache  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in ('OPENAI_BASE_URL', 'OPENAI_API_KEY', 'OLLAMA_API_KEY',
                'PODCAST_INDEX_API_KEY', 'PODCAST_INDEX_API_SECRET'):
        monkeypatch.delenv(var, raising=False)
    # get_effective_base_url reads through a 5s TTL cache; drop it so each
    # test sees the DB values it just wrote (the real save path invalidates
    # this cache on every provider PUT).
    invalidate_provider_cache()


@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c


def _response(status, body=b'', json_body=None):
    if json_body is not None:
        body = json.dumps(json_body).encode()
    r = MagicMock()
    r.status_code = status
    r.iter_content = lambda chunk_size: iter([body])
    return r


class TestProbeModelsEndpoint:
    def test_success(self):
        with patch('api.providers.safe_get',
                   return_value=_response(200, json_body={'data': []})) as sg:
            result = _probe_models_endpoint('http://localhost:11434/v1', 'sk-x')
        assert result['ok'] is True
        assert result['status'] == 200
        assert sg.call_args[0][0] == 'http://localhost:11434/v1/models'
        assert sg.call_args[1]['headers']['Authorization'] == 'Bearer sk-x'

    def test_no_key_sends_no_auth_header(self):
        with patch('api.providers.safe_get',
                   return_value=_response(200, json_body={'data': []})) as sg:
            _probe_models_endpoint('http://localhost:11434/v1', '')
        assert 'Authorization' not in sg.call_args[1]['headers']

    def test_404_points_at_path(self):
        with patch('api.providers.safe_get', return_value=_response(404)):
            result = _probe_models_endpoint('http://localhost:11434', '')
        assert result['ok'] is False
        assert result['reachable'] is True
        assert '/v1' in result['detail']

    def test_401_points_at_key(self):
        with patch('api.providers.safe_get', return_value=_response(401)):
            result = _probe_models_endpoint('http://server:8000/v1', '')
        assert result['reachable'] is True
        assert 'API key' in result['detail']

    def test_200_non_json_is_not_ok(self):
        with patch('api.providers.safe_get',
                   return_value=_response(200, body=b'<html>')):
            result = _probe_models_endpoint('http://some-web-server', '')
        assert result['ok'] is False
        assert 'OpenAI-compatible' in result['detail']

    def test_200_json_without_data_list_is_not_ok(self):
        # The real client reads response.data; JSON without it means
        # discovery would fail despite the 200.
        with patch('api.providers.safe_get',
                   return_value=_response(200, json_body={'error': 'nope'})):
            result = _probe_models_endpoint('http://server:8000/v1', '')
        assert result['ok'] is False
        assert 'model list' in result['detail']

    def test_401_with_key_names_saved_key(self):
        with patch('api.providers.safe_get', return_value=_response(401)):
            result = _probe_models_endpoint('http://server:8000/v1', 'sk-x')
        assert 'rejected the saved API key' in result['detail']

    def test_connection_error_unreachable(self):
        with patch('api.providers.safe_get',
                   side_effect=requests_lib.ConnectionError('refused')):
            result = _probe_models_endpoint('http://server:8000/v1', '')
        assert result['ok'] is False
        assert result['reachable'] is False


class TestLlmConnectionEndpoint:
    def _post(self, client, provider, body=None):
        return client.post(f'/api/v1/settings/providers/{provider}/test-connection',
                           data=json.dumps(body if body is not None else {}),
                           content_type='application/json')

    def test_unknown_provider_404(self, client):
        assert self._post(client, 'bogus').status_code == 404

    def test_ollama_base_url_normalized_to_v1(self, client):
        # The real client appends /v1 for Ollama; the probe must match.
        with patch('api.providers._probe_models_endpoint',
                   return_value={'ok': True, 'reachable': True,
                                 'status': 200, 'detail': 'Connected.'}) as probe:
            r = self._post(client, 'ollama',
                           {'baseUrl': 'http://localhost:11434'})
        assert r.status_code == 200
        assert probe.call_args[0][0] == 'http://localhost:11434/v1'

    def test_openai_base_url_not_normalized(self, client):
        with patch('api.providers._probe_models_endpoint',
                   return_value={'ok': True, 'reachable': True,
                                 'status': 200, 'detail': 'Connected.'}) as probe:
            self._post(client, 'openai', {'baseUrl': 'http://server:8000/v1'})
        assert probe.call_args[0][0] == 'http://server:8000/v1'

    def test_saved_key_withheld_from_other_server(self, client, temp_db):
        temp_db.set_secret('openai_api_key', 'sk-openai-saved')
        temp_db.set_setting('openai_base_url', 'http://server:8000/v1')
        with patch('api.providers._probe_models_endpoint',
                   return_value={'ok': True, 'reachable': True,
                                 'status': 200, 'detail': 'Connected.'}) as probe:
            self._post(client, 'openai', {'baseUrl': 'http://evil.example.com/v1'})
        assert probe.call_args[0][1] == ''

    def test_saved_key_sent_to_saved_server(self, client, temp_db):
        temp_db.set_secret('openai_api_key', 'sk-openai-saved')
        temp_db.set_setting('openai_base_url', 'http://server:8000/v1')
        with patch('api.providers._probe_models_endpoint',
                   return_value={'ok': True, 'reachable': True,
                                 'status': 200, 'detail': 'Connected.'}) as probe:
            self._post(client, 'openai', {'baseUrl': 'http://server:8000/v1'})
        assert probe.call_args[0][1] == 'sk-openai-saved'

    def test_empty_body_base_url_is_error(self, client):
        r = self._post(client, 'openai', {'baseUrl': ''})
        assert r.status_code == 200
        assert 'base URL' in r.get_json()['detail']

    def test_non_string_base_url_rejected(self, client):
        assert self._post(client, 'openai', {'baseUrl': 5}).status_code == 400

    def test_key_withheld_when_no_base_url_ever_saved(self, client, temp_db):
        # get_effective_base_url falls back to a hardcoded default; the key
        # gate must not treat that default as an operator-designated server.
        temp_db.set_secret('openai_api_key', 'sk-openai-saved')
        with patch('api.providers._probe_models_endpoint',
                   return_value={'ok': True, 'reachable': True,
                                 'status': 200, 'detail': 'Connected.'}) as probe:
            self._post(client, 'openai',
                       {'baseUrl': 'http://localhost:8000/v1'})
        assert probe.call_args[0][1] == ''

    def test_malformed_port_returns_staged_result_not_500(self, client):
        with patch('api.providers._probe_models_endpoint',
                   return_value={'ok': False, 'reachable': False,
                                 'detail': 'x'}):
            r = self._post(client, 'openai', {'baseUrl': 'http://host:99999999/v1'})
        assert r.status_code == 200

    def test_same_server_malformed_port_is_false(self):
        assert _same_server('http://host:notaport/v1',
                            'http://host:8000/v1') is False


class TestFixedProviderConnection:
    def _post(self, client, provider, body=None):
        return client.post(f'/api/v1/settings/providers/{provider}/test-connection',
                           data=json.dumps(body if body is not None else {}),
                           content_type='application/json')

    def test_anthropic_probes_fixed_url(self, client):
        with patch('api.providers.safe_get',
                   return_value=_response(200, json_body={'data': []})) as sg:
            r = self._post(client, 'anthropic')
        assert r.status_code == 200
        assert r.get_json()['ok'] is True
        assert sg.call_args[0][0] == 'https://api.anthropic.com/v1/models'

    def test_openrouter_probes_fixed_url(self, client):
        with patch('api.providers.safe_get',
                   return_value=_response(200, json_body={'data': {}})) as sg:
            r = self._post(client, 'openrouter')
        assert r.get_json()['ok'] is True
        assert sg.call_args[0][0] == 'https://openrouter.ai/api/v1/auth/key'

    def test_body_base_url_ignored_for_fixed_provider(self, client):
        # No baseUrl input exists for fixed providers; a body value must
        # never redirect the probe (or the saved key) anywhere else.
        with patch('api.providers.safe_get',
                   return_value=_response(200, json_body={'data': []})) as sg:
            self._post(client, 'anthropic',
                       {'baseUrl': 'http://evil.example.com'})
        assert sg.call_args[0][0] == 'https://api.anthropic.com/v1/models'

    def test_401_without_key(self, client):
        with patch('api.providers.safe_get', return_value=_response(401)):
            r = self._post(client, 'anthropic')
        data = r.get_json()
        assert data['ok'] is False
        assert data['reachable'] is True
        assert 'Save an API key' in data['detail']

    def test_401_with_saved_key(self, client, temp_db):
        temp_db.set_secret('anthropic_api_key', 'sk-ant-bad')
        with patch('api.providers.safe_get', return_value=_response(401)) as sg:
            r = self._post(client, 'anthropic')
        data = r.get_json()
        assert 'rejected the saved key' in data['detail']
        assert sg.call_args[1]['headers']['x-api-key'] == 'sk-ant-bad'

    def test_unreachable(self, client):
        with patch('api.providers.safe_get',
                   side_effect=requests_lib.ConnectionError('no route')):
            r = self._post(client, 'openrouter')
        data = r.get_json()
        assert data['ok'] is False
        assert data['reachable'] is False


class TestLegacyKeyTestOllamaNormalization:
    def test_test_route_normalizes_ollama_to_v1(self, client, temp_db):
        temp_db.set_secret('ollama_api_key', 'sk-ollama')
        temp_db.set_setting('openai_base_url', 'http://localhost:11434')
        with patch('api.providers.safe_get',
                   return_value=_response(200, json_body={'data': []})) as sg:
            r = client.post('/api/v1/settings/providers/ollama/test')
        assert r.status_code == 200
        assert sg.call_args[0][0] == 'http://localhost:11434/v1/models'


class TestPodcastIndexHeaders:
    def test_signature_shape(self):
        headers = _podcast_index_headers('key123', 'secret456')
        assert headers['X-Auth-Key'] == 'key123'
        expected = hashlib.sha1(
            ('key123' + 'secret456' + headers['X-Auth-Date']).encode()
        ).hexdigest()  # nosec B324 - mirrors the PodcastIndex contract
        assert headers['Authorization'] == expected


class TestPodcastIndexEndpoint:
    def _post(self, client):
        return client.post('/api/v1/settings/podcast-index/test')

    def test_no_credentials(self, client):
        r = self._post(client)
        assert r.status_code == 200
        data = r.get_json()
        assert data['ok'] is False
        assert 'save' in data['detail'].lower()

    def test_bad_credentials_401(self, client):
        with patch('api.podcast_search._get_podcast_index_credentials',
                   return_value=('k', 's')), \
             patch('api.podcast_search.safe_get',
                   return_value=_response(401, json_body={'status': 'false'})):
            r = self._post(client)
        data = r.get_json()
        assert data['ok'] is False
        assert data['reachable'] is True
        assert 'credentials' in data['detail']

    def test_valid_credentials(self, client):
        with patch('api.podcast_search._get_podcast_index_credentials',
                   return_value=('k', 's')), \
             patch('api.podcast_search.safe_get',
                   return_value=_response(200, json_body={'status': 'true',
                                                          'feeds': []})) as sg:
            r = self._post(client)
        data = r.get_json()
        assert data['ok'] is True
        assert data['status'] == 200
        assert sg.call_args[1]['headers']['X-Auth-Key'] == 'k'

    def test_unreachable(self, client):
        with patch('api.podcast_search._get_podcast_index_credentials',
                   return_value=('k', 's')), \
             patch('api.podcast_search.safe_get',
                   side_effect=requests_lib.ConnectionError('down')):
            r = self._post(client)
        data = r.get_json()
        assert data['ok'] is False
        assert data['reachable'] is False
