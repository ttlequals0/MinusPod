"""Settings surface for the none LLM provider."""
import os
import sys
import tempfile

import pytest

from tests.app_bootstrap import authenticate_test_client

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='llm-optional-settings-test-'))

import config_transfer  # noqa: E402
from api import get_database  # noqa: E402
from llm_client import invalidate_provider_cache  # noqa: E402

_KEYS = ('llm_provider', 'claude_model', 'secondary_provider', 'anthropic_api_key',
         'reviewer_calibration_on_change')


@pytest.fixture
def hdr(app_client):
    db = get_database()
    saved = {key: db.get_setting(key) for key in _KEYS}
    db.set_setting('reviewer_calibration_on_change', 'false', is_default=False)
    token = authenticate_test_client(app_client)
    yield token
    for key, value in saved.items():
        if value is None:
            db.clear_setting(key)
        else:
            db.set_setting(key, value, is_default=False)
    invalidate_provider_cache()


def _put(client, hdr, payload):
    return client.put('/api/v1/settings/ad-detection', json=payload, headers=hdr)


def test_put_accepts_llm_provider_none(app_client, hdr):
    assert _put(app_client, hdr, {'llmProvider': 'none'}).status_code == 200


def test_get_settings_llm_enabled_follows_provider(app_client, hdr):
    assert _put(app_client, hdr, {'llmProvider': 'none'}).status_code == 200
    body = app_client.get('/api/v1/settings').get_json()
    assert body['llmEnabled'] is False
    assert body['llmProvider']['value'] == 'none'
    assert _put(app_client, hdr, {'llmProvider': 'anthropic'}).status_code == 200
    assert app_client.get('/api/v1/settings').get_json()['llmEnabled'] is True


def test_secondary_provider_rejects_none(app_client, hdr):
    assert _put(app_client, hdr, {'secondaryProvider': 'none'}).status_code == 400


def test_failover_provider_rejects_none(app_client, hdr):
    assert _put(app_client, hdr, {'failoverLlmProvider': 'none'}).status_code == 400


def test_switching_to_none_and_back_keeps_model_and_key(app_client, hdr):
    db = get_database()
    db.set_setting('llm_provider', 'anthropic', is_default=False)
    db.set_setting('claude_model', 'claude-sonnet-4-5-20250929', is_default=False)
    db.set_setting('anthropic_api_key', 'sk-ant-keep', is_default=False)
    invalidate_provider_cache()
    assert _put(app_client, hdr, {'llmProvider': 'none'}).status_code == 200
    assert db.get_setting('claude_model') == 'claude-sonnet-4-5-20250929'
    assert _put(app_client, hdr, {'llmProvider': 'anthropic'}).status_code == 200
    assert db.get_setting('claude_model') == 'claude-sonnet-4-5-20250929'
    assert db.get_setting('anthropic_api_key')


def test_models_preview_for_none_is_empty(app_client, hdr):
    r = app_client.get('/api/v1/settings/models?provider=none', headers=hdr)
    assert r.status_code == 200
    assert r.get_json() == {'models': []}


def test_llm_disabled_response_shape(app_client):
    from api import llm_disabled_response
    with app_client.application.test_request_context():
        response = llm_disabled_response('needs a model')
    assert response.status_code == 409
    assert response.get_json() == {'error': 'needs a model', 'status': 409, 'code': 'llm_disabled'}


def test_switching_to_none_builds_no_client(app_client, hdr, monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError('client requested under none')
    monkeypatch.setattr('llm_client._build_client', boom)
    assert _put(app_client, hdr, {'llmProvider': 'none'}).status_code == 200
    assert app_client.get('/api/v1/settings').status_code == 200


def test_client_factory_refuses_none():
    import llm_client
    with pytest.raises(llm_client.LLMDisabledError):
        llm_client.get_client_for_provider('none')


@pytest.mark.parametrize('settings,status', [
    ({'llm_provider': 'none'}, 200),
    ({'secondary_provider': 'none'}, 400),
])
def test_config_import_provider_none(app_client, hdr, settings, status):
    db = get_database()
    document = config_transfer.export_config(db, '2.98.7')
    document['settings'] = settings
    preview = app_client.post('/api/v1/system/config-import/preview',
                              json={'document': document, 'scope': 'global'}, headers=hdr)
    if status == 400:
        assert preview.status_code == 400, preview.get_json()
        return
    assert preview.status_code == 200, preview.get_json()
    result = app_client.post('/api/v1/system/config-import',
                             json={'document': document, 'scope': 'global',
                                   'previewToken': preview.get_json()['previewToken']}, headers=hdr)
    assert result.status_code == 200, result.get_json()
    assert db.get_setting('llm_provider') == 'none'
