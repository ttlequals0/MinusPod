import pytest

import config_transfer
from config_transfer import ConfigTransferError


def _document(settings):
    return {
        'format': config_transfer.FORMAT,
        'formatVersion': config_transfer.FORMAT_VERSION,
        'appVersion': '2.98.2',
        'exportedAt': '2026-10-08T00:00:00+00:00',
        'containsSecrets': True,
        'settings': settings,
        'feeds': [],
    }


def test_export_preserves_model_inheritance_and_explicit_missing(temp_db):
    for key in ('claude_model', 'verification_model', 'chapters_model'):
        temp_db.clear_setting(key)
    temp_db.set_setting('pattern_cleanup_model', '', is_default=False)
    document = config_transfer.export_config(temp_db, '2.98.2')
    settings = document['settings']

    assert settings['pattern_cleanup_model'] == ''
    assert settings['verification_model'] is None
    assert settings['chapters_model'] is None
    assert settings['detection_provider'] == 'primary'
    assert settings['verification_provider'] == config_transfer.SAME_AS_DETECTION
    assert settings['chapters_provider'] == config_transfer.SAME_AS_DETECTION
    assert settings['claude_model'] == ''
    assert settings['system_prompt_override'] == ''


@pytest.mark.parametrize('key', sorted(config_transfer.NULLABLE_OPTIONAL_SETTINGS))
def test_document_accepts_only_allowlisted_nullable_optional_settings(key):
    config_transfer._validate_document(_document({key: None}))


def test_document_still_rejects_null_window_duration():
    with pytest.raises(ConfigTransferError, match='cannot be null'):
        config_transfer._validate_document(_document({'window_size_seconds': None}))


def test_currency_validation_and_import_normalization():
    config_transfer._validate_document(_document({'provider_budget_display_currency': 'eur'}))
    normalized = config_transfer._normalize_import_settings(
        {'provider_budget_display_currency': 'eur'})
    assert normalized['provider_budget_display_currency'] == 'EUR'
    with pytest.raises(ConfigTransferError, match='three-letter currency code'):
        config_transfer._validate_document(
            _document({'provider_budget_display_currency': 'EU1'}))
    with pytest.raises(ConfigTransferError, match='three-letter currency code'):
        config_transfer._validate_document(
            _document({'provider_budget_display_currency': '\u20acUR'}))


@pytest.mark.parametrize('address', [
    'recipient@example.com\r\nBcc:other@example.com',
    'recipient@',
    'recipient@example.com extra',
])
def test_email_recipient_validation_matches_native_address_policy(address):
    with pytest.raises(ConfigTransferError, match='invalid address'):
        config_transfer._validate_document(_document({'email_recipients': address}))


def test_retention_import_rejects_inconsistent_prospective_pair(temp_db):
    with pytest.raises(ConfigTransferError, match='must not exceed retention_days'):
        config_transfer.build_preview(
            temp_db, _document({'retention_days': 30, 'original_retention_days': 60}),
            'global')


def test_disabled_retention_preserves_larger_original_window(temp_db):
    preview = config_transfer.build_preview(
        temp_db, _document({'retention_days': 0, 'original_retention_days': 3650}),
        'global')
    assert preview['changedSettings']
