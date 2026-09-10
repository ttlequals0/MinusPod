"""OpenCode Go and Zen require an x-opencode-session header on every request (#719)."""
from unittest.mock import patch

import llm_client
from config import PROVIDER_OPENAI_COMPATIBLE


def _build(base_url):
    with patch.object(llm_client, 'get_effective_base_url', return_value=base_url), \
         patch.object(llm_client, 'get_effective_openai_api_key', return_value='k'):
        return llm_client._build_client(PROVIDER_OPENAI_COMPATIBLE)


def test_opencode_base_url_gets_session_and_client_headers():
    headers = _build('https://opencode.ai/zen/go/v1').extra_headers
    assert headers == {'x-opencode-session': llm_client._OPENCODE_SESSION_ID,
                       'x-opencode-client': 'minuspod'}


def test_other_endpoints_get_no_opencode_headers():
    assert _build('http://localhost:8000/v1').extra_headers == {}
    assert _build('https://notopencode.ai/v1').extra_headers == {}


def test_models_probe_sends_the_opencode_headers():
    from api.providers import _models_request
    _, headers = _models_request('https://opencode.ai/zen/go/v1', 'k')
    assert headers['x-opencode-session'] == llm_client._OPENCODE_SESSION_ID
    assert 'x-opencode-session' not in _models_request('http://localhost:8000/v1', 'k')[1]
