import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

import config_transfer
import secrets_crypto
from config_transfer import ConfigTransferError
from fx_rates import FxRate, FxRateError
from rate_limit_hold import active_held_pairs, record_hold_until
from utils.secret_writes import set_or_clear_secret


def _document(settings=None, feeds=None):
    return {
        'format': config_transfer.FORMAT,
        'formatVersion': config_transfer.FORMAT_VERSION,
        'appVersion': '2.98.2',
        'exportedAt': '2026-10-08T00:00:00+00:00',
        'containsSecrets': True,
        'settings': settings or {},
        'feeds': feeds or [],
    }


def _feed(slug='example-feed', source='https://example.com/feed.xml', **settings):
    return {
        'slug': slug,
        'feedType': 'subscribed',
        'settings': {'source_url': source, **settings},
    }


def test_export_includes_only_config_fields_and_round_trips_recents(temp_db):
    db = temp_db
    db.create_podcast('example-feed', 'https://example.com/feed.xml', 'Example')
    db.create_podcast('recents', 'recents://', 'Recent episodes', feed_type='recents')
    db.update_podcast('recents', description='Latest episodes')
    conn = db.get_connection()
    conn.execute("UPDATE podcasts SET last_refresh_error = 'runtime-only' WHERE slug = 'example-feed'")
    conn.commit()

    exported = config_transfer.export_config(db, '2.98.2')
    recents = next(feed for feed in exported['feeds'] if feed['slug'] == 'recents')
    assert recents['feedType'] == 'recents'
    assert recents['settings'] == {
        'title': 'Recent episodes',
        'description': 'Latest episodes',
        'source_url': 'recents://',
    }
    subscribed = next(feed for feed in exported['feeds'] if feed['slug'] == 'example-feed')
    assert 'last_refresh_error' not in subscribed['settings']
    assert 'user_tags' in subscribed['settings']


def test_export_budget_includes_worst_apply_envelope_and_utf8_bytes(temp_db, monkeypatch):
    db = temp_db
    db.create_podcast('example-feed', 'https://example.com/feed.xml', 'Example')
    document = config_transfer.export_config(db, '2.98.2')
    selected = [feed['slug'] for feed in document['feeds']]
    envelope = {
        'document': document,
        'scope': 'everything',
        'selectedFeeds': selected,
        'previewToken': '0' * 64,
    }
    encoded_size = len(config_transfer._canonical_json(envelope).encode('utf-8'))
    compact_file_size = len(json.dumps(
        document, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))
    assert compact_file_size < encoded_size

    monkeypatch.setattr(config_transfer, 'MAX_CONFIG_REQUEST_BYTES', encoded_size)
    config_transfer.export_config(db, '2.98.2')
    monkeypatch.setattr(config_transfer, 'MAX_CONFIG_REQUEST_BYTES', encoded_size - 1)
    with pytest.raises(ConfigTransferError, match='10 MiB request limit'):
        config_transfer.export_config(db, '2.98.2')


def test_large_valid_configuration_round_trips_within_request_limit(temp_db, monkeypatch):
    pricing = {f'model-{index}': {'inputCostPerMtok': 1, 'outputCostPerMtok': 2}
               for index in range(4500)}
    temp_db.set_setting('model_pricing_overrides', json.dumps(pricing))
    temp_db.create_podcast('example-local', 'local://example-local', 'Example', feed_type='local')
    description = 'Description. ' * 8000
    temp_db.update_podcast('example-local', description=description)
    document = config_transfer.export_config(temp_db, '2.98.2')
    preview = config_transfer.build_preview(temp_db, document, 'everything')
    monkeypatch.setattr(config_transfer, '_after_feed_commit', lambda *_args: [])

    config_transfer.apply_config(temp_db, document, 'everything', None, preview['previewToken'])

    assert temp_db.get_model_pricing_overrides() == pricing
    assert temp_db.get_podcast_by_slug('example-local')['description'] == description


@pytest.mark.parametrize('value', [8000.5, '8000.5', 'malformed', 'nan'])
def test_portable_integer_rejects_fractional_or_malformed_stored_values(value):
    with pytest.raises(ConfigTransferError, match='invalid stored integer'):
        config_transfer._portable_integer('audio_cue_freq_max_hz', value)


def test_local_empty_description_round_trips(temp_db, monkeypatch):
    temp_db.create_podcast('example-local', 'local://example-local', 'Example', feed_type='local')
    temp_db.update_podcast('example-local', description='')
    document = config_transfer.export_config(temp_db, '2.98.2')
    preview = config_transfer.build_preview(temp_db, document, 'feeds')
    monkeypatch.setattr(config_transfer, '_after_feed_commit', lambda *_args: [])

    config_transfer.apply_config(temp_db, document, 'feeds', None, preview['previewToken'])

    assert temp_db.get_podcast_by_slug('example-local')['description'] == ''


def test_partial_cue_only_import_uses_existing_mode_at_apply(temp_db, monkeypatch):
    temp_db.create_podcast('example-local', 'local://example-local', 'Example', feed_type='local')
    temp_db.update_podcast('example-local', detection_mode='cue_only')
    document = _document(feeds=[{
        'slug': 'example-local', 'feedType': 'local',
        'settings': {'source_url': 'local://example-local', 'title': 'Example', 'skip_transcription': True},
    }])
    preview = config_transfer.build_preview(temp_db, document, 'feeds')
    monkeypatch.setattr(config_transfer, '_after_feed_commit', lambda *_args: [])

    config_transfer.apply_config(temp_db, document, 'feeds', None, preview['previewToken'])

    assert temp_db.get_podcast_by_slug('example-local')['skip_transcription'] == 1


def test_feed_ceiling_uses_selected_prospective_globals(temp_db, monkeypatch):
    temp_db.set_setting('max_ad_duration_confirmed_seconds', '300')
    document = _document({'max_ad_duration_confirmed_seconds': 600}, [{
        'slug': 'example-local', 'feedType': 'local',
        'settings': {'source_url': 'local://example-local', 'title': 'Example',
                     'max_ad_duration_reject_override': 500},
    }])
    with pytest.raises(ConfigTransferError, match='exceeds the global limit'):
        config_transfer.build_preview(temp_db, document, 'feeds')
    preview = config_transfer.build_preview(temp_db, document, 'everything')
    monkeypatch.setattr(config_transfer, '_after_feed_commit', lambda *_args: [])

    config_transfer.apply_config(temp_db, document, 'everything', None, preview['previewToken'])

    assert temp_db.get_setting('max_ad_duration_confirmed_seconds') == '600.0'
    assert temp_db.get_podcast_by_slug('example-local')['max_ad_duration_reject_override'] == 500


def test_null_cannot_bypass_global_cross_field_validation(temp_db):
    temp_db.set_setting('window_size_seconds', '3600')
    temp_db.set_setting('window_overlap_seconds', '1500')
    with pytest.raises(ConfigTransferError, match='window_size_seconds cannot be null'):
        config_transfer.build_preview(temp_db, _document({'window_size_seconds': None}), 'global')
    assert temp_db.get_setting('window_size_seconds') == '3600'


def test_unknown_null_setting_is_skipped(temp_db):
    preview = config_transfer.build_preview(temp_db, _document({'future_setting': None}), 'global')
    assert preview['skippedUnknownSettings'] == ['future_setting']
    assert preview['changedSettings'] == []


def test_empty_email_event_selection_round_trips(temp_db, monkeypatch):
    temp_db.set_setting('email_events', '[]')
    document = config_transfer.export_config(temp_db, '2.98.2')
    preview = config_transfer.build_preview(temp_db, document, 'global')
    monkeypatch.setattr(config_transfer, '_after_feed_commit', lambda *_args: [])

    config_transfer.apply_config(temp_db, document, 'global', None, preview['previewToken'])

    assert temp_db.get_setting('email_events') == '[]'


@pytest.mark.parametrize('key,value', [
    ('email_smtp_username', 7), ('email_smtp_username', 'name\r\nheader'),
    ('email_smtp_from', 7), ('email_smtp_from', 'not-an-address'),
    ('email_smtp_from', 'sender@example.com\r\nheader'), ('email_events', [{}]),
])
def test_malformed_email_fields_fail_preview(temp_db, key, value):
    with pytest.raises(ConfigTransferError, match=key):
        config_transfer.build_preview(temp_db, _document({key: value}), 'global')


def test_export_fails_closed_on_unreadable_secret(temp_db, monkeypatch):
    temp_db.set_setting('openai_api_key', 'enc:v1:broken')

    def fail_decrypt(_db, _value):
        raise ValueError('invalid envelope')

    monkeypatch.setattr(config_transfer, 'decrypt', fail_decrypt)
    with pytest.raises(ValueError):
        config_transfer.export_config(temp_db, '2.98.2')


def test_currency_restore_refreshes_destination_rate_before_transaction(temp_db, monkeypatch):
    temp_db.set_setting('provider_budget_display_currency', 'USD')
    temp_db.set_setting('provider_budget_fx_rate', '1')
    document = _document({'provider_budget_display_currency': 'EUR'})
    preview = config_transfer.build_preview(temp_db, document, 'global')

    def get_rate(currency):
        assert currency == 'EUR'
        assert not temp_db.get_connection().in_transaction
        return FxRate('EUR', Decimal('0.87'), 'Frankfurter', '2026-10-08')

    monkeypatch.setattr(config_transfer, 'get_usd_rate', get_rate)
    config_transfer.apply_config(temp_db, document, 'global', None, preview['previewToken'])

    assert temp_db.get_setting('provider_budget_display_currency') == 'EUR'
    assert temp_db.get_setting('provider_budget_fx_rate') == '0.87'
    assert temp_db.get_setting('provider_budget_fx_source_date') == '2026-10-08'
    assert temp_db.get_setting('provider_budget_fx_fetched_at')


def test_currency_lookup_failure_preserves_all_settings(temp_db, monkeypatch):
    temp_db.set_setting('provider_budget_display_currency', 'USD')
    temp_db.set_setting('provider_budget_fx_rate', '1')
    original_retention = temp_db.get_setting('retention_days')
    document = _document({'provider_budget_display_currency': 'EUR', 'retention_days': 60})
    preview = config_transfer.build_preview(temp_db, document, 'global')

    def fail_rate(_currency):
        raise FxRateError('Could not load currency data')

    monkeypatch.setattr(config_transfer, 'get_usd_rate', fail_rate)
    with pytest.raises(ConfigTransferError, match='Could not load currency data') as raised:
        config_transfer.apply_config(temp_db, document, 'global', None, preview['previewToken'])

    assert raised.value.status == 503
    assert temp_db.get_setting('provider_budget_display_currency') == 'USD'
    assert temp_db.get_setting('provider_budget_fx_rate') == '1'
    assert temp_db.get_setting('retention_days') == original_retention


def test_pricing_restore_normalizes_ids_and_replaces_destination_entries(temp_db):
    temp_db.set_setting('model_pricing_overrides', json.dumps({
        'old-model': {'inputCostPerMtok': 1, 'outputCostPerMtok': 2}}))
    document = _document({'model_pricing_overrides': {
        ' new-model ': {'inputCostPerMtok': 3, 'outputCostPerMtok': 4},
        'removed-model': None,
    }})
    preview = config_transfer.build_preview(temp_db, document, 'global')
    config_transfer.apply_config(temp_db, document, 'global', None, preview['previewToken'])

    assert json.loads(temp_db.get_setting('model_pricing_overrides')) == {
        'new-model': {'inputCostPerMtok': 3.0, 'outputCostPerMtok': 4.0}}


def test_budget_reserve_validates_prospective_settings(temp_db):
    temp_db.set_setting('provider_budget_enabled', 'true')
    temp_db.set_setting('provider_budget_unknown_cost', 'reserve')
    temp_db.set_setting('provider_budget_unknown_reserve_microusd', '10')
    with pytest.raises(ConfigTransferError, match='must be positive for reserve'):
        config_transfer.build_preview(temp_db, _document({
            'provider_budget_unknown_reserve_microusd': '0'}), 'global')


@pytest.mark.parametrize('value', [None, ''])
def test_cleanup_model_restore_preserves_inherit_and_missing(temp_db, value):
    temp_db.set_setting('pattern_cleanup_model', 'old-model')
    document = _document({'pattern_cleanup_model': value})
    preview = config_transfer.build_preview(temp_db, document, 'global')
    config_transfer.apply_config(temp_db, document, 'global', None, preview['previewToken'])

    assert temp_db.get_setting('pattern_cleanup_model') == value
    assert config_transfer.export_config(temp_db, '2.98.2')['settings']['pattern_cleanup_model'] == value


def test_preview_reports_missing_cleanup_model_changing_to_inheritance(temp_db):
    temp_db.set_setting('pattern_cleanup_model', '')
    document = _document({'pattern_cleanup_model': None})
    preview = config_transfer.build_preview(temp_db, document, 'global')
    assert preview['changedSettings'] == [
        {'key': 'pattern_cleanup_model', 'kind': 'setting', 'secret': False}]
    temp_db.clear_setting('pattern_cleanup_model')
    preview = config_transfer.build_preview(temp_db, document, 'global')
    assert preview['changedSettings'] == []


def test_nullable_models_remain_inherited_during_provider_restore(temp_db, monkeypatch):
    temp_db.set_setting('llm_provider', 'anthropic')
    temp_db.set_setting('verification_model', 'old-model')
    temp_db.set_setting('chapters_model', 'old-model')
    monkeypatch.setattr('api.settings._probe_after_provider_change', lambda: None)
    document = _document({'llm_provider': 'ollama', 'claude_model': 'new-model',
                          'verification_model': None, 'chapters_model': None})
    preview = config_transfer.build_preview(temp_db, document, 'global')
    config_transfer.apply_config(temp_db, document, 'global', None, preview['previewToken'])

    assert temp_db.get_setting('verification_model') is None
    assert temp_db.get_setting('chapters_model') is None


@pytest.mark.parametrize('secret_key,provider', [
    ('anthropic_api_key', 'anthropic'), ('openai_api_key', 'openai-compatible'),
    ('ollama_api_key', 'ollama'),
])
@pytest.mark.parametrize('new_value', ['replacement-key', None])
def test_restored_primary_credential_clears_only_its_hold(
        temp_db, monkeypatch, secret_key, provider, new_value):
    monkeypatch.setenv('MINUSPOD_MASTER_PASSPHRASE', 'test-passphrase')
    monkeypatch.setattr(secrets_crypto, '_dek_cache', None)
    set_or_clear_secret(temp_db, secret_key, 'previous-key')
    retry_at = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    record_hold_until(temp_db, provider, retry_at)
    record_hold_until(temp_db, provider, retry_at, credential_slot='secondary')
    document = _document({secret_key: new_value})
    preview = config_transfer.build_preview(temp_db, document, 'global')

    config_transfer.apply_config(temp_db, document, 'global', None, preview['previewToken'])

    assert (provider, 'primary') not in active_held_pairs(temp_db)
    assert (provider, 'secondary') in active_held_pairs(temp_db)


def test_preview_masks_secret_values_and_skips_unknown_settings(temp_db):
    document = _document({'retention_days': 45, 'future_setting': 'ignored'})
    preview = config_transfer.build_preview(temp_db, document, 'global')

    assert preview['changedSettings'] == [{'kind': 'setting', 'key': 'retention_days', 'secret': False}]
    assert preview['skippedUnknownSettings'] == ['future_setting']
    assert preview['preservesTargetOnlyFeeds'] is True


def test_scoped_feed_import_preserves_target_only_and_does_not_change_globals(temp_db, monkeypatch):
    db = temp_db
    original_retention = db.get_setting('retention_days')
    db.create_podcast('selected-feed', 'https://example.com/selected.xml', 'Before')
    db.create_podcast('target-only', 'https://example.com/target.xml', 'Keep')
    document = _document({'retention_days': 45}, [
        _feed('selected-feed', 'https://example.com/selected.xml', description='Imported'),
    ])
    preview = config_transfer.build_preview(db, document, 'feeds', ['selected-feed'])
    monkeypatch.setattr(config_transfer, '_after_feed_commit', lambda *_args: [])

    result = config_transfer.apply_config(
        db, document, 'feeds', ['selected-feed'], preview['previewToken'])

    assert result['updatedFeeds'] == ['selected-feed']
    assert db.get_setting('retention_days') == original_retention
    assert db.get_podcast_by_slug('selected-feed')['description'] == 'Imported'
    assert db.get_podcast_by_slug('target-only')['title'] == 'Keep'


def test_everything_import_creates_local_and_updates_existing_recents(temp_db, monkeypatch):
    db = temp_db
    db.create_podcast('recents', 'recents://', 'Old recents', feed_type='recents')
    local = {
        'slug': 'local-archive',
        'feedType': 'local',
        'settings': {'source_url': 'local://local-archive', 'title': 'Archive',
                     'description': 'Stored locally', 'author': 'Author'},
    }
    recent = {
        'slug': 'recents',
        'feedType': 'recents',
        'settings': {'source_url': 'recents://', 'title': 'Imported recents',
                     'description': 'Recent items'},
    }
    document = _document({'retention_days': 50}, [local, recent])
    preview = config_transfer.build_preview(db, document, 'everything')
    monkeypatch.setattr(config_transfer, '_after_feed_commit', lambda *_args: [])

    config_transfer.apply_config(db, document, 'everything', None, preview['previewToken'])

    assert db.get_podcast_by_slug('local-archive')['author'] == 'Author'
    assert db.get_podcast_by_slug('local-archive')['source_url'] == 'local://local-archive'
    assert db.get_podcast_by_slug('recents')['title'] == 'Imported recents'
    assert db.get_setting('retention_days') == '50'


def test_late_feed_failure_rolls_back_settings_and_staged_feed(temp_db, monkeypatch):
    db = temp_db
    original_retention = db.get_setting('retention_days')
    document = _document({'retention_days': 45}, [_feed()])
    preview = config_transfer.build_preview(db, document, 'everything')
    original_create = db.create_podcast

    def create_then_fail(*args, **kwargs):
        original_create(*args, **kwargs)
        raise RuntimeError('injected failure')

    monkeypatch.setattr(db, 'create_podcast', create_then_fail)
    with pytest.raises(RuntimeError, match='injected failure'):
        config_transfer.apply_config(db, document, 'everything', None, preview['previewToken'])

    assert db.get_setting('retention_days') == original_retention
    assert db.get_podcast_by_slug('example-feed') is None


def test_stale_destination_rejects_apply(temp_db):
    db = temp_db
    document = _document({'retention_days': 45})
    preview = config_transfer.build_preview(db, document, 'global')
    db.set_setting('retention_days', '46')

    with pytest.raises(ConfigTransferError, match='Destination changed'):
        config_transfer.apply_config(
            db, document, 'global', None, preview['previewToken'])


def test_preview_token_is_single_use(temp_db):
    db = temp_db
    document = _document({'retention_days': 45})
    preview = config_transfer.build_preview(db, document, 'global')

    config_transfer.apply_config(db, document, 'global', None, preview['previewToken'])
    with pytest.raises(ConfigTransferError, match='Destination changed'):
        config_transfer.apply_config(db, document, 'global', None, preview['previewToken'])


def test_forbidden_runtime_key_is_rejected_without_write(temp_db):
    document = _document({'provider_config_revision': '9'})

    with pytest.raises(ConfigTransferError, match='non-transferable'):
        config_transfer.build_preview(temp_db, document, 'global')
    assert temp_db.get_setting('provider_config_revision') is None


def test_preview_does_not_resolve_or_fetch_feed_urls(temp_db, monkeypatch):
    document = _document(feeds=[_feed()])

    def fail(*_args, **_kwargs):
        raise AssertionError('preview must not perform network activity')

    monkeypatch.setattr('utils.safe_http._validate_for_tier', fail)
    monkeypatch.setattr('rss_parser.RSSParser.fetch_feed', fail)
    preview = config_transfer.build_preview(temp_db, document, 'everything')

    assert preview['selectedFeedSlugs'] == ['example-feed']


def test_apply_uses_feed_ssrf_policy_and_private_host_opt_in(temp_db, monkeypatch):
    from utils.safe_http import URLTrust

    observed = []
    monkeypatch.setattr(config_transfer, '_validate_for_tier',
                        lambda _url, trust: observed.append(trust))
    document = _document(feeds=[_feed()])
    preview = config_transfer.build_preview(temp_db, document, 'everything')

    config_transfer._validate_import_feed_urls(config_transfer._resolve_scope(
        config_transfer._validate_document(document), 'everything', None)[1])
    assert observed[-1] is URLTrust.FEED_CONTENT

    monkeypatch.setenv('MINUSPOD_ALLOW_PRIVATE_FEED_HOSTS', 'true')
    config_transfer._validate_import_feed_urls(config_transfer._resolve_scope(
        config_transfer._validate_document(document), 'everything', None)[1])
    assert observed[-1] is URLTrust.OPERATOR_CONFIGURED
    assert preview['selectedFeedSlugs'] == ['example-feed']


def test_apply_rejects_feed_url_ssrf_before_writes(temp_db, monkeypatch):
    from utils.safe_http import URLTrust
    from utils.url import SSRFError

    def reject(_url, trust):
        assert trust is URLTrust.FEED_CONTENT
        raise SSRFError('Blocked private IP')

    monkeypatch.delenv('MINUSPOD_ALLOW_PRIVATE_FEED_HOSTS', raising=False)
    monkeypatch.setattr(config_transfer, '_validate_for_tier', reject)
    document = _document({'retention_days': 45}, [_feed()])
    original_retention = temp_db.get_setting('retention_days')
    preview = config_transfer.build_preview(temp_db, document, 'everything')

    with pytest.raises(ConfigTransferError, match='security validation'):
        config_transfer.apply_config(
            temp_db, document, 'everything', None, preview['previewToken'])

    assert temp_db.get_setting('retention_days') == original_retention
    assert temp_db.get_podcast_by_slug('example-feed') is None


def test_runtime_config_download_route_sets_attachment_and_cache_headers(temp_db, monkeypatch):
    from flask import Flask
    from api import api, init_limiter

    document = _document({'retention_days': 45})
    monkeypatch.setattr('api.config_transfer.get_database', lambda: temp_db)
    monkeypatch.setattr('api.get_database', lambda: temp_db)
    monkeypatch.setattr('api.config_transfer.export_config', lambda *_args: document)
    app = Flask(__name__)
    app.secret_key = 'test-session-key'
    app.register_blueprint(api)
    init_limiter(app)

    client = app.test_client()
    response = client.get('/api/v1/system/config-backup')

    assert response.status_code == 200
    assert response.mimetype == 'application/json'
    assert response.headers['Cache-Control'] == 'no-store, private'
    assert 'attachment' in response.headers['Content-Disposition']
    assert json.loads(response.get_data()) == document

    from api.auth_state import SESSION_GENERATION_KEY, current_generation
    temp_db.set_setting('app_password', 'configured-password')
    with client.session_transaction() as session:
        session['authenticated'] = True
        session[SESSION_GENERATION_KEY] = current_generation(temp_db)
    rejected = client.post('/api/v1/system/config-import/preview', json={})
    assert rejected.status_code == 403


def test_local_p20_json_object_survives_export_import_round_trip(temp_db, monkeypatch):
    db = temp_db
    db.create_podcast('local-archive', 'local://local-archive', 'Archive', feed_type='local')
    p20 = {
        'medium': 'podcast',
        'locked': 'yes',
        'guid': 'a1b2c3d4-e5f6-4789-8abc-def012345678',
    }
    db.update_podcast('local-archive', p20_channel_json=json.dumps(p20))
    db.update_podcast(
        'local-archive', title_skip_patterns='["special-*", "season-*"]',
        segment_category_actions='{"sponsor":"remove"}')
    exported = config_transfer.export_config(db, '2.98.2')
    local = next(feed for feed in exported['feeds'] if feed['slug'] == 'local-archive')
    local['settings']['p20_channel_json'] = p20
    db.update_podcast('local-archive', p20_channel_json='{}')
    monkeypatch.setattr('api.feeds._p20_tag_attrs', lambda: {})
    preview = config_transfer.build_preview(db, exported, 'everything')
    monkeypatch.setattr(config_transfer, '_after_feed_commit', lambda *_args: [])

    config_transfer.apply_config(
        db, exported, 'everything', None, preview['previewToken'])

    assert json.loads(db.get_podcast_by_slug('local-archive')['p20_channel_json']) == p20
    restored = db.get_podcast_by_slug('local-archive')
    assert json.loads(restored['title_skip_patterns']) == ['special-*', 'season-*']
    assert json.loads(restored['segment_category_actions']) == {'sponsor': 'remove'}


@pytest.mark.parametrize('secret_key', [
    'openrouter_api_key', 'secondary_provider_api_key', 'whisper_api_key',
    'failover_llm_api_key', 'failover_whisper_api_key', 'podcast_index_api_key',
])
def test_explicit_null_clears_managed_secret_but_missing_preserves_it(
        temp_db, monkeypatch, secret_key):
    import secrets_crypto
    from utils.secret_writes import set_or_clear_secret

    db = temp_db
    monkeypatch.setenv('MINUSPOD_MASTER_PASSPHRASE', 'test-passphrase')
    monkeypatch.setattr(secrets_crypto, '_dek_cache', None)
    set_or_clear_secret(db, secret_key, 'configured-key')
    configured = db.get_setting(secret_key)

    missing = _document()
    missing_preview = config_transfer.build_preview(db, missing, 'global')
    config_transfer.apply_config(
        db, missing, 'global', None, missing_preview['previewToken'])
    assert db.get_setting(secret_key) == configured

    explicit_null = _document({secret_key: None})
    null_preview = config_transfer.build_preview(db, explicit_null, 'global')
    config_transfer.apply_config(
        db, explicit_null, 'global', None, null_preview['previewToken'])
    assert db.get_setting(secret_key) is None


def test_exported_typed_configuration_round_trips_through_preview_and_apply(
        temp_db, monkeypatch):
    db = temp_db
    db.set_setting('segment_category_actions', '{"sponsor":"mark"}')
    db.set_setting('community_sync_categories', '["sponsor"]')
    db.set_setting('jit_blocked_user_agents', '["ExampleBot"]')
    db.set_setting('podping_nodes', '["https://rpc.example.com/"]')
    db.set_setting('chapter_boundary_max_tokens', '2400')
    db.set_setting('audio_cue_freq_max_hz', '8000.0')
    db.set_setting('review_provider', 'secondary')
    db.set_setting('reviewer_reasoning_budget', '8192')
    db.set_setting('reviewer_reasoning_level', 'high')
    db.set_setting('min_trim_threshold', '20.0')
    db.set_setting('system_prompt_override', '{"keep":"this prompt as text"}')
    exported = config_transfer.export_config(db, '2.98.2')
    assert exported['settings']['min_trim_threshold'] == 20.0
    assert exported['settings']['system_prompt_override'] == '{"keep":"this prompt as text"}'
    assert exported['settings']['audio_cue_freq_max_hz'] == 8000
    preview = config_transfer.build_preview(db, exported, 'global')
    monkeypatch.setattr(config_transfer, '_after_feed_commit', lambda *_args: [])

    config_transfer.apply_config(
        db, exported, 'global', None, preview['previewToken'])

    after = config_transfer.export_config(db, '2.98.2')
    assert after['settings'] == exported['settings']


def test_unavailable_crypto_rejects_secret_import_before_any_write(temp_db, monkeypatch):
    import secrets_crypto

    db = temp_db
    monkeypatch.delenv('MINUSPOD_MASTER_PASSPHRASE', raising=False)
    monkeypatch.setattr(secrets_crypto, '_dek_cache', None)
    original_retention = db.get_setting('retention_days')
    local = {
        'slug': 'local-archive',
        'feedType': 'local',
        'settings': {'source_url': 'local://local-archive', 'title': 'Archive'},
    }
    document = _document(
        {'retention_days': 45, 'openrouter_api_key': 'sk-or-configured'}, [local])
    preview = config_transfer.build_preview(db, document, 'everything')

    with pytest.raises(ConfigTransferError, match='encryption is unavailable'):
        config_transfer.apply_config(
            db, document, 'everything', None, preview['previewToken'])

    assert db.get_setting('retention_days') == original_retention
    assert db.get_podcast_by_slug('local-archive') is None


def test_preview_validates_nullable_feed_overrides_and_effective_processing_mode(temp_db):
    valid = _document(feeds=[_feed(
        cue_create_from_pairs_override=True,
        differential_fetch_enabled=False,
        keep_original_audio_override=True,
    )])
    config_transfer.build_preview(temp_db, valid, 'everything')

    invalid_bool = _document(feeds=[_feed(keep_original_audio_override='unexpected')])
    with pytest.raises(ConfigTransferError, match='keep_original_audio_override'):
        config_transfer.build_preview(temp_db, invalid_bool, 'everything')

    invalid_mode = _document(feeds=[_feed(skip_transcription=True)])
    with pytest.raises(ConfigTransferError, match='requires cue_only'):
        config_transfer.build_preview(temp_db, invalid_mode, 'everything')

    invalid_range = _document(feeds=[_feed(ad_detection_exclude_start_override=0.5)])
    with pytest.raises(ConfigTransferError, match='0 or between 1 and 600'):
        config_transfer.build_preview(temp_db, invalid_range, 'everything')


def test_preview_rejects_nonfinite_feed_numbers(temp_db):
    document = _document(feeds=[_feed(cue_snap_confidence_override=float('nan'))])

    with pytest.raises(ConfigTransferError, match='must be finite'):
        config_transfer.build_preview(temp_db, document, 'everything')


def test_post_commit_reports_failed_refresh_outcome(temp_db, monkeypatch):
    monkeypatch.setenv('DATA_DIR', str(temp_db.data_dir))
    from main_app import feeds as main_feeds

    temp_db.create_podcast('example-feed', 'https://example.com/feed.xml', 'Example')
    monkeypatch.setattr(main_feeds, 'invalidate_feed_cache', lambda: None)
    monkeypatch.setattr(
        main_feeds, 'refresh_rss_feed',
        lambda *_args, **_kwargs: main_feeds.RefreshOutcome(False, 'fetch_failed'))

    warnings = config_transfer._after_feed_commit(
        temp_db, ['example-feed'], [], {'example-feed'})

    assert warnings == ['One or more served feeds could not be refreshed after commit.']


def test_phase_less_cleanup_and_timezone_settings_restore_with_schedule_anchor(
        temp_db, monkeypatch):
    db = temp_db
    values = {
        'pattern_cleanup_enabled': True,
        'pattern_cleanup_cron': ' 0 3 * * * ',
        'pattern_cleanup_batch_size': 50,
        'pattern_cleanup_unused_days': 120,
        'pattern_cleanup_provider': 'b',
        'pattern_cleanup_model': ' imported-model ',
        'notification_timezone': 'Europe/London',
    }
    assert not set(values) & config_transfer._setting_payload(values).keys()
    assert not set(values) & config_transfer._phase_applied_setting_keys(values)
    document = _document(values)

    old_anchor = '2000-01-01T00:00:00+00:00'
    db.set_setting('pattern_cleanup_enabled', 'false')
    db.set_setting('pattern_cleanup_schedule_anchor', old_anchor)
    db.set_setting('notification_timezone', 'UTC')
    preview = config_transfer.build_preview(db, document, 'global')
    monkeypatch.setattr(config_transfer, '_after_feed_commit', lambda *_args: [])

    config_transfer.apply_config(db, document, 'global', None, preview['previewToken'])

    assert db.get_setting('pattern_cleanup_enabled') == 'true'
    assert db.get_setting('pattern_cleanup_cron') == '0 3 * * *'
    assert db.get_setting('pattern_cleanup_batch_size') == '50'
    assert db.get_setting('pattern_cleanup_unused_days') == '120'
    assert db.get_setting('pattern_cleanup_provider') == 'secondary'
    assert db.get_setting('pattern_cleanup_model') == 'imported-model'
    assert db.get_setting('notification_timezone') == 'Europe/London'
    anchor = db.get_setting('pattern_cleanup_schedule_anchor')
    assert anchor != old_anchor

    next_preview = config_transfer.build_preview(db, document, 'global')
    config_transfer.apply_config(db, document, 'global', None, next_preview['previewToken'])
    assert db.get_setting('pattern_cleanup_schedule_anchor') == anchor


def test_export_import_restores_effective_nonregistry_defaults(temp_db, monkeypatch):
    db = temp_db
    exported = config_transfer.export_config(db, '2.98.2')
    source = exported['settings']
    assert source['email_enabled'] is False
    assert source['email_events']
    assert source['email_smtp_port'] == 587
    assert source['email_smtp_security'] == 'starttls'
    assert source['webhooks'] == []
    assert source['retention_days'] == 30
    assert source['original_retention_days'] == 30
    assert source['community_sync_enabled'] is False
    assert source['db_backup_enabled'] is False
    assert source['db_backup_keep_count'] == 1
    assert source['update_check_enabled'] is True
    assert source['update_channel'] == 'stable'
    assert source['update_patterns_from_reviewer_adjustments'] is True
    assert source['min_trim_threshold'] == 20.0
    assert source['pattern_cleanup_provider'] == ''
    assert source['pattern_cleanup_model'] is None
    assert 'feed_auth_key' not in source

    conflicting = {
        'email_enabled': 'true',
        'email_events': '[]',
        'email_smtp_host': 'mail.example.com',
        'email_smtp_port': '2525',
        'email_smtp_security': 'ssl',
        'email_smtp_username': 'operator',
        'email_smtp_from': 'operator@example.com',
        'email_recipients': 'alerts@example.com',
        'webhooks': '[{"id":"hook","url":"https://example.com/hook","events":["Episode Failed"]}]',
        'retention_days': '90',
        'original_retention_days': '60',
        'community_sync_enabled': 'true',
        'community_sync_cron': '0 1 * * *',
        'db_backup_enabled': 'true',
        'db_backup_cron': '0 1 * * *',
        'db_backup_dest': '/var/backups/minuspod',
        'db_backup_keep_count': '4',
        'update_check_enabled': 'false',
        'update_channel': 'edge',
        'update_patterns_from_reviewer_adjustments': 'false',
        'min_trim_threshold': '60.0',
        'model_pricing_overrides': '{"custom/model":{"inputCostPerMtok":1,"outputCostPerMtok":2}}',
        'pattern_cleanup_provider': 'secondary',
        'pattern_cleanup_model': 'target-only-model',
    }
    for key, value in conflicting.items():
        db.set_setting(key, value)
    timezone_value = source['notification_timezone']
    db.set_setting('notification_timezone', 'UTC' if timezone_value != 'UTC' else 'America/New_York')
    preview = config_transfer.build_preview(db, exported, 'global')
    monkeypatch.setattr(config_transfer, '_after_feed_commit', lambda *_args: [])

    config_transfer.apply_config(db, exported, 'global', None, preview['previewToken'])

    assert db.get_setting('email_enabled') == 'false'
    assert db.get_setting('email_smtp_host') == ''
    assert json.loads(db.get_setting('email_events')) == source['email_events']
    assert db.get_setting('webhooks') == '[]'
    assert db.get_setting('retention_days') == '30'
    assert db.get_setting('original_retention_days') == '30'
    assert db.get_setting('community_sync_enabled') == 'false'
    assert db.get_setting('community_sync_cron') == source['community_sync_cron']
    assert db.get_setting('db_backup_enabled') == 'false'
    assert db.get_setting('db_backup_cron') == source['db_backup_cron']
    assert db.get_setting('db_backup_dest') == ''
    assert db.get_setting('db_backup_keep_count') == '1'
    assert db.get_setting('update_check_enabled') == 'true'
    assert db.get_setting('update_channel') == 'stable'
    assert db.get_setting('update_patterns_from_reviewer_adjustments') == 'true'
    assert float(db.get_setting('min_trim_threshold')) == 20.0
    assert db.get_model_pricing_overrides() == {}
    assert db.get_setting('pattern_cleanup_provider') is None
    assert db.get_setting('pattern_cleanup_model') is None
    assert db.get_setting('notification_timezone') == timezone_value


@pytest.mark.parametrize(('key', 'value', 'message'), [
    ('pattern_cleanup_enabled', None, 'cannot be null'),
    ('pattern_cleanup_cron', 'bad cron', 'cron is invalid'),
    ('pattern_cleanup_batch_size', 0, 'batch_size is out of range'),
    ('pattern_cleanup_unused_days', 1, 'unused_days is out of range'),
    ('pattern_cleanup_provider', 'invalid', 'provider is invalid'),
    ('pattern_cleanup_model', 'x' * 201, '200 characters or fewer'),
    ('notification_timezone', None, 'cannot be null'),
])
def test_phase_less_settings_are_validated_before_preview(temp_db, key, value, message):
    document = _document({key: value})
    with pytest.raises(ConfigTransferError, match=message):
        config_transfer.build_preview(temp_db, document, 'global')


@pytest.mark.parametrize('feed_type', ['subscribed', 'local'])
def test_audio_output_import_preserves_missing_and_resets_null(temp_db, monkeypatch, feed_type):
    monkeypatch.setattr(config_transfer, '_after_feed_commit', lambda *_args: [])
    source = 'local://example-feed' if feed_type == 'local' else 'https://example.com/feed.xml'
    temp_db.create_podcast('example-feed', source, feed_type=feed_type)
    temp_db.set_setting('audio_replacement_sound_enabled', 'false')
    temp_db.set_setting('audio_mp3_stream_copy_enabled', 'true')
    temp_db.update_podcast('example-feed', audio_replacement_sound_override=False,
                           audio_mp3_stream_copy_override=True)
    original = config_transfer.export_config(temp_db, '2.99.0')
    assert original['settings']['audio_replacement_sound_enabled'] is False
    assert original['settings']['audio_mp3_stream_copy_enabled'] is True
    feed = next(f for f in original['feeds'] if f['slug'] == 'example-feed')
    assert feed['settings']['audio_replacement_sound_override'] is False
    assert feed['settings']['audio_mp3_stream_copy_override'] is True
    document = _document(feeds=[{
        'slug': 'example-feed', 'feedType': feed_type,
        'settings': {'source_url': source, 'title': 'Updated title'},
    }])
    preview = config_transfer.build_preview(temp_db, document, 'everything')
    config_transfer.apply_config(temp_db, document, 'everything', None, preview['previewToken'])
    assert temp_db.get_setting_bool('audio_replacement_sound_enabled') is False
    assert temp_db.get_setting_bool('audio_mp3_stream_copy_enabled') is True
    assert temp_db.resolve_audio_output('example-feed') == {
        'replacement_sound_enabled': False, 'mp3_stream_copy_enabled': True,
    }
    document['settings'] = {'audio_replacement_sound_enabled': None, 'audio_mp3_stream_copy_enabled': None}
    document['feeds'][0]['settings'].update(audio_replacement_sound_override=None,
                                          audio_mp3_stream_copy_override=None)
    preview = config_transfer.build_preview(temp_db, document, 'everything')
    config_transfer.apply_config(temp_db, document, 'everything', None, preview['previewToken'])
    assert temp_db.resolve_audio_output('example-feed') == {
        'replacement_sound_enabled': True, 'mp3_stream_copy_enabled': False,
    }


def test_duration_filters_export_import_and_range_validation(temp_db, monkeypatch):
    temp_db.create_podcast('example-feed', 'https://example.com/feed.xml', 'Example')
    temp_db.update_podcast('example-feed', min_duration_seconds=60, max_duration_seconds=180)
    document = config_transfer.export_config(temp_db, '2.98.2')
    settings = document['feeds'][0]['settings']
    assert settings['min_duration_seconds'] == 60
    assert settings['max_duration_seconds'] == 180
    temp_db.update_podcast('example-feed', min_duration_seconds=None, max_duration_seconds=None)
    preview = config_transfer.build_preview(temp_db, document, 'everything')
    monkeypatch.setattr(config_transfer, '_after_feed_commit', lambda *_args: [])
    config_transfer.apply_config(temp_db, document, 'everything', None, preview['previewToken'])
    assert temp_db.get_podcast_by_slug('example-feed')['min_duration_seconds'] == 60
    settings['min_duration_seconds'] = 200
    with pytest.raises(ConfigTransferError, match='must not exceed'):
        config_transfer.build_preview(temp_db, document, 'everything')


@pytest.mark.parametrize('override', ['Feed/2.0', None])
def test_feed_download_ua_round_trip(temp_db, override, monkeypatch):
    temp_db.create_podcast('example-feed', 'https://example.com/feed.xml', 'Example')
    temp_db.update_podcast('example-feed', download_user_agent_override=override)
    monkeypatch.setattr(config_transfer, '_after_feed_commit', lambda *_args: [])
    document = config_transfer.export_config(temp_db, '2.99.3')
    assert document['feeds'][0]['settings']['download_user_agent_override'] == override
    temp_db.update_podcast('example-feed', download_user_agent_override='Changed/1.0')
    preview = config_transfer.build_preview(temp_db, document, 'feeds')
    config_transfer.apply_config(temp_db, document, 'feeds', None, preview['previewToken'])
    assert temp_db.get_podcast_by_slug('example-feed')['download_user_agent_override'] == override


@pytest.mark.parametrize('override', ['Feed/2.0', None])
def test_feed_feed_ua_round_trip(temp_db, override, monkeypatch):
    temp_db.create_podcast('example-feed', 'https://example.com/feed.xml', 'Example')
    temp_db.update_podcast('example-feed', feed_user_agent_override=override)
    monkeypatch.setattr(config_transfer, '_after_feed_commit', lambda *_args: [])
    document = config_transfer.export_config(temp_db, '2.99.3')
    assert document['feeds'][0]['settings']['feed_user_agent_override'] == override
    temp_db.update_podcast('example-feed', feed_user_agent_override='Changed/1.0')
    preview = config_transfer.build_preview(temp_db, document, 'feeds')
    config_transfer.apply_config(temp_db, document, 'feeds', None, preview['previewToken'])
    assert temp_db.get_podcast_by_slug('example-feed')['feed_user_agent_override'] == override
