"""Settings surface for the none LLM provider."""
import os
import sys
import tempfile
import threading
from types import SimpleNamespace

import pytest

from tests.unit.llm_optional_support import restoring_auth

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='llm-optional-settings-test-'))

import config_transfer  # noqa: E402
from api import get_database  # noqa: E402
from api.settings import provider_phases  # noqa: E402
import llm_client  # noqa: E402
from llm_client import get_effective_provider, invalidate_provider_cache  # noqa: E402


@pytest.fixture(autouse=True)
def _no_background_threads(monkeypatch):
    """Provider saves start probe and verify threads; none may run or outlive a test."""
    class InertThread:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            pass

    fake = SimpleNamespace(**{**vars(threading), 'Thread': InertThread})
    for module in (llm_client, provider_phases):
        monkeypatch.setattr(module, 'threading', fake)


@pytest.fixture
def hdr(app_client):
    """Auth headers; every settings row the test touches is restored afterwards."""
    yield from restoring_auth(app_client)


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


class _ListingClient:
    """A stand-in provider client that lists one model."""

    def list_models(self):
        return [SimpleNamespace(id='m-listed', name='Listed', created=0)]


def test_secondary_model_listing_stays_available_under_none(app_client, hdr, monkeypatch):
    # Deliberate: an operator picks a model for a slot before switching back from none.
    db = get_database()
    db.set_setting('llm_provider', 'none', is_default=False)
    invalidate_provider_cache()
    monkeypatch.setattr('api.settings.providers.create_client_for_provider',
                        lambda *args, **kwargs: _ListingClient())
    r = app_client.get('/api/v1/settings/models?provider=openai-compatible&slot=secondary', headers=hdr)
    assert r.status_code == 200
    assert [m['id'] for m in r.get_json()['models']] == ['m-listed']


def test_secondary_model_refresh_stays_available_under_none(app_client, hdr, monkeypatch):
    db = get_database()
    db.set_setting('llm_provider', 'none', is_default=False)
    db.set_setting('secondary_provider', 'openai-compatible', is_default=False)
    invalidate_provider_cache()
    monkeypatch.setattr('api.settings.providers.get_client_for_provider',
                        lambda *args, **kwargs: _ListingClient())
    r = app_client.post('/api/v1/settings/models/refresh', json={'slot': 'secondary'}, headers=hdr)
    assert r.status_code == 200
    body = r.get_json()
    assert body['count'] == 1 and [m['id'] for m in body['models']] == ['m-listed']


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


def _seed_provider(db, provider, base_url=None, model='model-a'):
    db.set_setting('llm_provider', provider, is_default=False)
    if base_url is not None:
        db.set_setting('openai_base_url', base_url, is_default=False)
    db.set_setting('claude_model', model, is_default=False)
    invalidate_provider_cache()


_STAGE_MODEL_FIELDS = ('claude_model', 'verification_model', 'chapters_model',
                       'review_model', 'pattern_cleanup_model')


def _seed_all_stage_models(db, provider, base_url=None):
    """Five stage models; chapters runs on the secondary slot."""
    _seed_provider(db, provider, base_url, 'm-detect')
    db.set_setting('secondary_provider', 'ollama', is_default=False)
    db.set_setting('secondary_provider_base_url', 'http://example.net:11434', is_default=False)
    db.set_setting('secondary_provider_enabled', 'true', is_default=False)
    db.set_setting('chapters_provider', 'secondary', is_default=False)
    for key in _STAGE_MODEL_FIELDS[1:]:
        db.set_setting(key, f'm-{key}', is_default=False)
    db.set_setting('review_provider', 'primary', is_default=False)


def _stage_models(db):
    return {key: db.get_setting(key) for key in _STAGE_MODEL_FIELDS}


def test_leaving_none_for_a_different_account_clears_models(app_client, hdr):
    db = get_database()
    _seed_all_stage_models(db, 'anthropic')
    assert _put(app_client, hdr, {'llmProvider': 'none'}).status_code == 200
    assert _put(app_client, hdr, {'llmProvider': 'openai-compatible'}).status_code == 200
    models = _stage_models(db)
    assert models.pop('chapters_model') == 'm-chapters_model'
    assert set(models.values()) == {''}, models


def test_leaving_none_for_the_same_endpoint_keeps_models(app_client, hdr):
    db = get_database()
    _seed_all_stage_models(db, 'openai-compatible', 'http://example.com:8000/v1')
    before = _stage_models(db)
    assert _put(app_client, hdr, {'llmProvider': 'none'}).status_code == 200
    assert _stage_models(db) == before
    assert _put(app_client, hdr, {'llmProvider': 'openai-compatible'}).status_code == 200
    assert _stage_models(db) == before
    assert not db.get_setting('llm_pre_none_identity')


def test_none_from_env_then_a_provider_clears_stale_models(app_client, hdr):
    db = get_database()
    _seed_all_stage_models(db, 'anthropic')
    db.clear_setting('llm_provider')
    db.clear_setting('llm_pre_none_identity')
    os.environ['LLM_PROVIDER'] = 'none'
    try:
        invalidate_provider_cache()
        assert _put(app_client, hdr, {'llmProvider': 'openai-compatible'}).status_code == 200
    finally:
        del os.environ['LLM_PROVIDER']
        invalidate_provider_cache()
    models = _stage_models(db)
    assert models.pop('chapters_model') == 'm-chapters_model'
    assert set(models.values()) == {''}, models


def test_none_from_env_then_a_provider_clears_every_primary_stage(app_client, hdr):
    db = get_database()
    _seed_all_stage_models(db, 'anthropic')
    db.set_setting('chapters_provider', 'primary', is_default=False)
    db.clear_setting('llm_provider')
    db.clear_setting('llm_pre_none_identity')
    os.environ['LLM_PROVIDER'] = 'none'
    try:
        invalidate_provider_cache()
        assert _put(app_client, hdr, {'llmProvider': 'openai-compatible'}).status_code == 200
    finally:
        del os.environ['LLM_PROVIDER']
        invalidate_provider_cache()
    assert set(_stage_models(db).values()) == {''}, _stage_models(db)


def test_clearing_stage_models_reports_only_stages_that_changed(app_client, hdr):
    from api.settings import ad_detection
    db = get_database()
    _seed_all_stage_models(db, 'anthropic')
    db.set_setting('verification_model', '', is_default=False)
    changed = []
    ad_detection._clear_stage_models(db, ad_detection._MODEL_STAGES, {}, changed)
    assert 'verification' not in changed
    assert 'detection' in changed


def test_leaving_none_keeps_models_set_in_the_same_payload(app_client, hdr):
    db = get_database()
    _seed_all_stage_models(db, 'anthropic')
    db.clear_setting('llm_pre_none_identity')
    _seed_provider(db, 'none', model='old')
    r = _put(app_client, hdr, {'llmProvider': 'openai-compatible', 'claudeModel': 'fresh'})
    assert r.status_code == 200
    assert db.get_setting('claude_model') == 'fresh'
    assert db.get_setting('verification_model') == ''


def test_fresh_install_none_then_provider_has_no_stale_model(app_client, hdr):
    db = get_database()
    for key in (*_STAGE_MODEL_FIELDS, 'llm_pre_none_identity'):
        db.clear_setting(key)
    assert _put(app_client, hdr, {'llmProvider': 'none'}).status_code == 200
    assert _put(app_client, hdr, {'llmProvider': 'anthropic'}).status_code == 200
    assert not db.get_setting('claude_model')


def test_leaving_none_for_another_base_url_clears_models(app_client, hdr):
    db = get_database()
    _seed_provider(db, 'openai-compatible', 'http://example.com:8000/v1', 'qwen3:8b')
    assert _put(app_client, hdr, {'llmProvider': 'none'}).status_code == 200
    assert _put(app_client, hdr, {'llmProvider': 'openai-compatible',
                                  'openaiBaseUrl': 'http://example.org:9000/v1'}).status_code == 200
    assert db.get_setting('claude_model') == ''


def test_first_selection_after_none_with_nothing_stored(app_client, hdr):
    db = get_database()
    db.clear_setting('llm_pre_none_identity')
    _seed_provider(db, 'none', model='')
    assert _put(app_client, hdr, {'llmProvider': 'ollama'}).status_code == 200
    assert db.get_setting('llm_provider') == 'ollama'
    assert db.get_setting('claude_model') == ''


def test_pre_none_identity_is_not_exported(app_client, hdr):
    db = get_database()
    _seed_provider(db, 'anthropic')
    assert _put(app_client, hdr, {'llmProvider': 'none'}).status_code == 200
    assert db.get_setting('llm_pre_none_identity')
    document = config_transfer.export_config(db, '2.98.7')
    assert 'llm_pre_none_identity' not in document['settings']


def test_secondary_test_connection_rejects_none(app_client, hdr):
    r = app_client.post('/api/v1/settings/providers/secondary/test-connection',
                        json={'provider': 'none'}, headers=hdr)
    assert r.status_code == 400


@pytest.mark.parametrize('suffix', ['test', 'test-connection'])
def test_provider_test_routes_do_not_know_none(app_client, hdr, suffix):
    r = app_client.post(f'/api/v1/settings/providers/none/{suffix}', json={}, headers=hdr)
    assert r.status_code == 404
    assert r.get_json()['error'] == 'unknown provider'


def test_models_route_accepts_none(app_client, hdr):
    assert app_client.get('/api/v1/settings/models?provider=none', headers=hdr).status_code == 200


def test_secondary_error_lists_only_secondary_providers(app_client, hdr):
    r = app_client.put('/api/v1/settings/ad-detection', json={'secondaryProvider': 'bogus'}, headers=hdr)
    assert r.status_code == 400
    assert 'none' not in r.get_json()['error']


def test_env_seeded_none_disables_the_llm(app_client, hdr, monkeypatch):
    db = get_database()
    db.clear_setting('llm_provider')
    monkeypatch.setenv('LLM_PROVIDER', 'none')
    invalidate_provider_cache()
    assert get_effective_provider() == 'none'
    body = app_client.get('/api/v1/settings').get_json()
    assert body['llmEnabled'] is False
    assert body['llmProvider']['value'] == 'none'


@pytest.mark.parametrize('stored', ['not json', '[1, 2]', '"text"', '7', 'null', '{"claude_model": "x"}'])
def test_malformed_pre_none_identity_means_nothing_stored(stored):
    from api.settings import ad_detection
    db = get_database()
    db.set_setting('llm_pre_none_identity', stored, is_default=False)
    try:
        assert ad_detection._stored_pre_none_identities(db) == {}
    finally:
        db.clear_setting('llm_pre_none_identity')


def test_settings_reset_clears_the_pre_none_identity(app_client, hdr):
    db = get_database()
    _seed_provider(db, 'anthropic')
    assert _put(app_client, hdr, {'llmProvider': 'none'}).status_code == 200
    assert db.get_setting('llm_pre_none_identity')
    assert app_client.post('/api/v1/settings/ad-detection/reset', headers=hdr).status_code == 200
    assert not db.get_setting('llm_pre_none_identity')


def _openapi_enum(path, method, name=None):
    import pathlib
    import yaml
    spec = yaml.safe_load(
        (pathlib.Path(__file__).resolve().parents[2] / 'openapi.yaml').read_text())
    operation = spec['paths'][path][method]
    if name is None:
        schema = operation['requestBody']['content']['application/json']['schema']
        return tuple(schema['properties']['provider']['enum'])
    return tuple(next(p for p in operation['parameters'] if p['name'] == name)['schema']['enum'])


def test_openapi_secondary_test_connection_enum_matches_the_handler():
    from api.providers import _SECONDARY_PROVIDER_TYPES
    enum = _openapi_enum('/settings/providers/secondary/test-connection', 'post')
    assert set(enum) == set(_SECONDARY_PROVIDER_TYPES)


def test_openapi_models_provider_enum_matches_the_handler():
    from api.settings import common
    enum = _openapi_enum('/settings/models', 'get', 'provider')
    assert set(enum) == set(common.VALID_LLM_PROVIDERS)
