import json

import pytest

import llm_client
import provider_probe
import failover
import api.settings as settings_api
import config_transfer
import systemone.settings as settings_module
from config import (
    PROVIDER_SYSTEMONE_COMPATIBLE, PROVIDER_TYPESAFE,
    SYSTEMONE_TUNABLE_DEFAULTS,
)
from systemone.tuning import SystemOneSettingsError, merge_profile, profile_from_snapshot


def test_partial_profile_update_preserves_omitted_values():
    defaults = SYSTEMONE_TUNABLE_DEFAULTS[PROVIDER_TYPESAFE]
    current = {**defaults, 'detectionEnter': 0.91, 'categoryContext': 4}
    updated = merge_profile(defaults, current, {'categoryContext': 5})
    assert updated['detectionEnter'] == 0.91
    assert updated['categoryContext'] == 5


def test_profile_validation_rejects_nonfinite_and_invalid_threshold_order():
    defaults = SYSTEMONE_TUNABLE_DEFAULTS[PROVIDER_TYPESAFE]
    with pytest.raises(SystemOneSettingsError, match='finite number'):
        merge_profile(defaults, defaults, {'detectionEnter': float('nan')})
    with pytest.raises(SystemOneSettingsError, match='must not exceed'):
        merge_profile(defaults, defaults, {'detectionStay': 0.96})


def test_missing_older_profile_field_uses_destination_value(temp_db):
    temp_db.set_setting(
        'systemone_tunables_primary_typesafe',
        json.dumps({**SYSTEMONE_TUNABLE_DEFAULTS[PROVIDER_TYPESAFE], 'categoryContext': 6}),
        is_default=False)
    normalized = config_transfer._normalize_import_settings(
        {'systemone_tunables_primary_typesafe': {'detectionEnter': 0.92}}, temp_db)
    imported = normalized['systemone_tunables_primary_typesafe']
    assert imported['detectionEnter'] == 0.92
    assert imported['categoryContext'] == 6


def test_profile_null_resets_and_nullable_field_null_is_retained(temp_db):
    data = {'systemOneTunables': {'primary': {'typesafe': {
        'detectionEnter': 0.90,
    }}}}
    values, issue = settings_api._systemone_tunable_values(temp_db, data)
    assert issue is None
    profile, is_default = values['systemone_tunables_primary_typesafe']
    assert profile['detectionEnter'] == 0.90
    assert is_default is False

    reset, issue = settings_api._systemone_tunable_values(
        temp_db, {'systemOneTunables': {'primary': {'typesafe': None}}})
    assert issue is None
    assert reset['systemone_tunables_primary_typesafe'] == (
        SYSTEMONE_TUNABLE_DEFAULTS[PROVIDER_TYPESAFE], True)


def test_profiles_are_registered_for_all_slot_provider_combinations(temp_db):
    snapshot = temp_db.get_all_settings()
    for slot in ('primary', 'secondary'):
        for provider in (PROVIDER_TYPESAFE, PROVIDER_SYSTEMONE_COMPATIBLE):
            profile = profile_from_snapshot(snapshot, slot, provider)
            assert profile == SYSTEMONE_TUNABLE_DEFAULTS[provider]


def test_accessor_uses_single_snapshot_and_native_timeout_default(temp_db, monkeypatch):
    calls = []
    monkeypatch.setattr(settings_module.database, 'Database', lambda: temp_db)
    original = temp_db.get_all_settings

    def read_snapshot():
        calls.append(1)
        return original()

    monkeypatch.setattr(temp_db, 'get_all_settings', read_snapshot)
    settings = settings_module.get_systemone_settings(
        PROVIDER_TYPESAFE, 'primary', 'jev-latest')
    assert settings.request_timeout == 60.0
    assert settings.request_deadline_seconds == 75.0
    assert len(calls) == 1


def test_enabled_chapters_rejects_systemone_route_with_effective_identity(temp_db):
    temp_db.set_setting('llm_provider', 'typesafe')
    temp_db.set_setting('claude_model', 'jev-latest')
    temp_db.set_setting('chapters_model', 'jev-latest')
    issue = settings_api._validate_systemone_phase_enables(temp_db, {'chaptersEnabled': True})
    assert issue == (
        "chapters is unsupported for effective provider 'typesafe' and model 'jev-latest'; "
        'choose a supported chat provider and model', 400)
    assert settings_api._validate_systemone_phase_enables(
        temp_db, {'chaptersEnabled': False}) is None


def test_global_chapter_mode_off_allows_systemone_until_feed_override(temp_db):
    temp_db.set_setting('llm_provider', 'typesafe')
    temp_db.set_setting('claude_model', 'jev-latest')
    temp_db.set_setting('chapters_model', 'jev-latest')
    temp_db.set_setting('chapters_enabled', 'true')
    temp_db.set_setting('chapters_mode', 'off')
    assert settings_api._validate_systemone_phase_enables(
        temp_db, {'chaptersEnabled': True, 'chaptersMode': 'off'}) is None
    issue = settings_api._validate_systemone_phase_enables(
        temp_db, {'chaptersEnabled': True, 'chaptersMode': 'off'},
        {'example-feed': 'generate'})
    assert issue and issue[0].startswith('chapters is unsupported')


def test_feed_only_import_cannot_enable_unsupported_chapters(temp_db):
    slug = 'systemone-chapter-import'
    temp_db.create_podcast(slug, 'https://example.com/feed.xml', 'Example podcast')
    temp_db.set_setting('llm_provider', 'typesafe')
    temp_db.set_setting('chapters_model', 'jev-latest')
    temp_db.set_setting('chapters_enabled', 'true')
    temp_db.set_setting('chapters_mode', 'off')
    document = config_transfer.export_config(temp_db, '2.98.2')
    feed = next(item for item in document['feeds'] if item['slug'] == slug)
    feed['settings']['chapters_mode'] = 'generate'
    with pytest.raises(config_transfer.ConfigTransferError, match='chapters is unsupported'):
        config_transfer.build_preview(temp_db, document, 'feeds', [slug])


def test_enabled_cleanup_rejects_known_jev_model_through_compatible_provider(temp_db):
    temp_db.set_setting('llm_provider', 'openai-compatible')
    temp_db.set_setting('claude_model', 'jev-preview')
    issue = settings_api._validate_systemone_phase_enables(
        temp_db, {'patternCleanupEnabled': True})
    assert issue and issue[0].startswith('pattern_cleanup is unsupported')


def test_failover_cannot_target_systemone_provider(temp_db):
    issue = settings_api._validate_systemone_phase_enables(temp_db, {
        'failoverLlmEnabled': True,
        'failoverLlmProvider': 'typesafe',
        'failoverLlmDetectionModel': 'jev-latest',
    })
    assert issue and issue[0].startswith('failover LLM is chat-only')


@pytest.mark.parametrize('slot', ['primary', 'secondary'])
def test_native_fractional_timeout_roundtrips_and_chat_limits_stay_int(temp_db, slot):
    provider_field = 'llmProvider' if slot == 'primary' else 'secondaryProvider'
    timeout_field = 'providerATimeoutSeconds' if slot == 'primary' else 'providerBTimeoutSeconds'
    key = 'llm_timeout_seconds' if slot == 'primary' else 'secondary_llm_timeout_seconds'
    data = {provider_field: 'typesafe', timeout_field: 0.125}
    assert settings_api._validate_failover_settings_payload(data, temp_db) is None
    assert settings_api._apply_provider_timeout_fields(temp_db, data) is None
    assert temp_db.get_setting(key) == '0.125'
    native = settings_module.get_systemone_settings(PROVIDER_TYPESAFE, slot, 'jev-latest')
    assert native.request_timeout == 0.125
    for invalid in [0, -1, float('nan'), float('inf'), True]:
        assert settings_api._validate_failover_settings_payload(
            {provider_field: 'typesafe', timeout_field: invalid}, temp_db)
    assert settings_api._validate_failover_settings_payload(
        {provider_field: 'openai-compatible', timeout_field: 0.125}, temp_db)


def test_operation_controls_defaults_validation_and_old_import_preservation(temp_db):
    defaults = SYSTEMONE_TUNABLE_DEFAULTS[PROVIDER_TYPESAFE]
    assert defaults['maxConcurrentOperations'] == 4
    assert defaults['retryAfterMaxSeconds'] == 5.0
    for field, invalid in [('maxConcurrentOperations', 0), ('maxConcurrentOperations', 1.5),
                           ('retryAfterMaxSeconds', -1), ('retryAfterMaxSeconds', float('inf'))]:
        with pytest.raises(SystemOneSettingsError):
            merge_profile(defaults, defaults, {field: invalid})
    current = merge_profile(defaults, defaults, {'maxConcurrentOperations': 2, 'retryAfterMaxSeconds': 0.25})
    temp_db.set_setting('systemone_tunables_primary_typesafe', json.dumps(current), is_default=False)
    imported = config_transfer._normalize_import_settings(
        {'systemone_tunables_primary_typesafe': {'categoryPass': False}}, temp_db)
    result = imported['systemone_tunables_primary_typesafe']
    assert result['maxConcurrentOperations'] == 2
    assert result['retryAfterMaxSeconds'] == 0.25


def test_typesafe_missing_key_cannot_build_or_probe_and_does_not_use_openai_key(temp_db, monkeypatch):
    monkeypatch.delenv('TYPESAFE_API_KEY', raising=False)
    monkeypatch.setenv('OPENAI_API_KEY', 'unrelated-key')
    monkeypatch.setattr(llm_client, 'get_effective_systemone_api_key', lambda _provider: None)
    monkeypatch.setattr(llm_client, 'get_effective_secondary_provider_api_key', lambda: None)
    for slot in ['primary', 'secondary']:
        assert llm_client.create_client_for_provider('typesafe', credential_slot=slot) is None
        result = provider_probe.probe_systemone_endpoint(
            'typesafe', None, '', 'jev-latest', credential_slot=slot, db=temp_db)
        assert result['ok'] is False and result['reachable'] is False
    assert temp_db.get_connection().execute('SELECT COUNT(*) FROM llm_call_usage').fetchone()[0] == 0


@pytest.mark.parametrize('enabled, mode', [(False, 'off'), (False, 'auto'), (True, 'auto')])
def test_phase_validation_reads_feeds_only_for_enabled_global_off_mode(temp_db, monkeypatch, enabled, mode):
    monkeypatch.setattr(temp_db, 'get_all_podcasts',
                        lambda: (_ for _ in ()).throw(AssertionError('feed rows cannot affect this mode')))
    settings_api._validate_systemone_phase_enables(temp_db, {'chaptersEnabled': enabled, 'chaptersMode': mode})


def test_native_probes_resolve_each_slots_configured_stage_and_freeze_health_model(temp_db, monkeypatch):
    values = {'llm_provider': 'systemone-compatible', 'secondary_provider_enabled': 'true',
              'secondary_provider': 'systemone-compatible', 'claude_model': 'jev-latest',
              'verification_model': 'jev-preview', 'detection_provider': 'primary',
              'verification_provider': 'secondary', 'systemone_base_url': 'https://example.com/a',
              'secondary_provider_base_url': 'https://example.com/b'}
    for key, value in values.items():
        temp_db.set_setting(key, value, is_default=False)
    primary = failover._capture_probe_context(temp_db, 'llm:primary')
    secondary = failover._capture_probe_context(temp_db, 'llm:secondary')
    assert primary['request_config']['model'] == 'jev-latest'
    assert secondary['request_config']['model'] == 'jev-preview'
    temp_db.set_setting('verification_model', 'changed-model', is_default=False)
    assert secondary['request_config']['model'] == 'jev-preview'
    assert secondary['config_identity'] != failover._capture_probe_context(temp_db, 'llm:secondary')['config_identity']
    temp_db.set_setting('verification_provider', 'primary', is_default=False)
    monkeypatch.setattr(provider_probe, 'probe_systemone_endpoint',
                        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError('missing model cannot probe')))
    config = failover._capture_probe_context(temp_db, 'llm:secondary')['request_config']
    assert failover.probe_target('llm:secondary', config)['reachable'] is None


def test_native_frozen_health_snapshot_resolves_independent_typesafe_key(temp_db, monkeypatch):
    monkeypatch.setenv('TYPESAFE_API_KEY', 'typesafe-only')
    monkeypatch.setenv('OPENAI_API_KEY', 'unrelated-key')
    temp_db.set_setting('llm_provider', 'typesafe', is_default=False)
    temp_db.set_setting('claude_model', 'jev-preview', is_default=False)
    config = failover._capture_probe_context(temp_db, 'llm:primary')['request_config']
    assert config['api_key'] == 'typesafe-only' and config['model'] == 'jev-preview'


def test_probe_can_select_explicit_review_slot_model_without_pass_models(temp_db):
    for key, value in {'llm_provider': 'typesafe', 'claude_model': '', 'verification_model': '',
                       'review_provider': 'primary', 'review_model': 'jev-preview'}.items():
        temp_db.set_setting(key, value, is_default=False)
    assert failover._capture_probe_context(temp_db, 'llm:primary')['request_config']['model'] == 'jev-preview'
    temp_db.set_setting('review_provider', 'same_as_pass', is_default=False)
    assert failover._capture_probe_context(temp_db, 'llm:primary')['request_config']['model'] is None


def test_fractional_native_timeout_export_and_preview_preserve_value(temp_db):
    temp_db.set_setting('llm_provider', 'typesafe', is_default=False)
    temp_db.set_setting('claude_model', 'jev-latest', is_default=False)
    temp_db.set_setting('llm_timeout_seconds', '0.125', is_default=False)
    temp_db.set_setting('chapters_enabled', 'false', is_default=False)
    temp_db.set_setting('pattern_cleanup_enabled', 'false', is_default=False)
    document = config_transfer.export_config(temp_db, '2.98.7')
    assert document['settings']['llm_timeout_seconds'] == 0.125
    preview = config_transfer.build_preview(temp_db, document, 'global')
    assert preview is not None
    temp_db.set_setting('llm_timeout_seconds', '120', is_default=False)
    temp_db.set_setting('llm_provider', 'openai-compatible', is_default=False)
    document = config_transfer.export_config(temp_db, '2.98.7')
    assert type(document['settings']['llm_timeout_seconds']) is int
    assert config_transfer.build_preview(temp_db, document, 'global') is not None


def test_review_cap_and_context_keep_source_positive_finite_startup_domain():
    defaults = SYSTEMONE_TUNABLE_DEFAULTS[PROVIDER_TYPESAFE]
    for value in [0.125, 600.25, 750.5]:
        profile = merge_profile(defaults, defaults, {'reviewBoundaryCapSeconds': value,
                                                    'reviewContextSeconds': value})
        assert profile['reviewBoundaryCapSeconds'] == value
        assert profile['reviewContextSeconds'] == value


@pytest.mark.parametrize('provider', ['typesafe', 'systemone-compatible'])
def test_env_only_native_primary_cannot_enable_global_or_import_chat_only_stages(temp_db, monkeypatch, provider):
    temp_db.clear_setting('llm_provider')
    monkeypatch.setenv('LLM_PROVIDER', provider)
    temp_db.set_setting('claude_model', 'configured-model', is_default=False)
    temp_db.set_setting('chapters_model', 'configured-model', is_default=False)
    temp_db.set_setting('pattern_cleanup_model', 'configured-model', is_default=False)
    temp_db.set_setting('chapters_enabled', 'false', is_default=False)
    temp_db.set_setting('pattern_cleanup_enabled', 'false', is_default=False)
    for payload in [{'chaptersEnabled': True, 'chaptersMode': 'auto'}, {'patternCleanupEnabled': True}]:
        issue = settings_api._validate_systemone_phase_enables(temp_db, payload)
        assert issue and 'unsupported' in issue[0]
    document = config_transfer.export_config(temp_db, '2.98.7')
    document['settings']['chapters_enabled'] = True
    document['settings']['chapters_mode'] = 'auto'
    with pytest.raises(config_transfer.ConfigTransferError, match='chapters is unsupported'):
        config_transfer.build_preview(temp_db, document, 'global')
    assert settings_api._validate_systemone_phase_enables(
        temp_db, {'llmProvider': 'openai-compatible', 'chaptersEnabled': True, 'chaptersMode': 'auto'}) is None


@pytest.mark.parametrize('slot', ['primary', 'secondary'])
def test_missing_compatible_base_is_unconfigured_without_openai_url_fallback(temp_db, monkeypatch, slot):
    monkeypatch.delenv('SYSTEMONE_BASE_URL', raising=False)
    monkeypatch.setenv('OPENAI_BASE_URL', 'https://example.com/unrelated')
    monkeypatch.setattr(llm_client, '_get_cached_setting', lambda _key: None)
    with pytest.raises(ValueError, match='requires a base URL'):
        llm_client._build_client('systemone-compatible', credential_slot=slot)
    config = {'provider': 'systemone-compatible', 'base_url': '', 'api_key': '', 'model': 'selected-model'}
    result = failover.probe_target('llm:' + slot, config)
    assert result['reachable'] is None and result['status'] is None
    assert temp_db.get_connection().execute('SELECT COUNT(*) FROM llm_call_usage').fetchone()[0] == 0


@pytest.mark.parametrize('profile_patch', [None, {'detectionEnter': 0.92}])
def test_real_profile_import_keeps_reset_metadata_and_omitted_newer_fields(temp_db, monkeypatch, profile_patch):
    key = 'systemone_tunables_primary_typesafe'
    defaults = SYSTEMONE_TUNABLE_DEFAULTS[PROVIDER_TYPESAFE]
    current = {**defaults, 'maxConcurrentOperations': 2, 'retryAfterMaxSeconds': 0.25}
    temp_db.set_setting(key, json.dumps(current), is_default=False)
    temp_db.set_setting('chapters_enabled', 'false', is_default=False)
    temp_db.set_setting('pattern_cleanup_enabled', 'false', is_default=False)
    document = config_transfer.export_config(temp_db, '2.98.7')
    document['settings'] = {key: profile_patch}
    preview = config_transfer.build_preview(temp_db, document, 'global')
    monkeypatch.setattr(settings_api, 'trigger_reviewer_calibration', lambda *_args: None)
    config_transfer.apply_config(temp_db, document, 'global', None, preview['previewToken'])
    stored = temp_db.get_all_settings()[key]
    profile = json.loads(stored['value'])
    assert stored['is_default'] is (profile_patch is None)
    if profile_patch is None:
        assert profile == defaults
    else:
        assert profile['detectionEnter'] == 0.92
        assert profile['maxConcurrentOperations'] == 2
        assert profile['retryAfterMaxSeconds'] == 0.25
