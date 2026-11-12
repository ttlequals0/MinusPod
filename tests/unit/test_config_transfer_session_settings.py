from tests.app_bootstrap import bootstrap
_test_data_dir = bootstrap('session_transfer_test_')
import pytest
import config_transfer


def test_session_keys_are_portable():
    assert 'session_lifetime_hours' in config_transfer.PORTABLE_SETTINGS
    assert 'session_remember_days' in config_transfer.PORTABLE_SETTINGS


def _import_error(key, value):
    with pytest.raises(config_transfer.ConfigTransferError) as caught:
        config_transfer._validate_setting_value(key, value)
    return str(caught.value)


def test_import_messages_use_the_setting_key():
    assert _import_error('session_lifetime_hours', 0) == 'session_lifetime_hours must be between 1 and 8760'
    assert _import_error('session_remember_days', 366) == 'session_remember_days must be between 1 and 365'
    assert _import_error('fingerprint_min_coverage', 2.0) == 'fingerprint_min_coverage must be a number between 0.1 and 1'


@pytest.mark.parametrize('key,default', [('session_lifetime_hours', 24), ('session_remember_days', 30)])
@pytest.mark.parametrize('stored', ['', '  ', 'abc'])
def test_export_reads_an_unusable_stored_session_value_as_the_default(key, default, stored):
    from database import Database
    db = Database()
    db.set_setting(key, stored, is_default=True)
    try:
        assert config_transfer.export_config(db, '2.0.0')['settings'][key] == default
    finally:
        db.reset_setting(key)


def test_empty_env_var_is_unset_for_session_seeding(monkeypatch):
    import config
    monkeypatch.setenv('SESSION_LIFETIME_HOURS', '')
    monkeypatch.setenv('SESSION_REMEMBER_DAYS', '')
    assert config.resolve_env_backed_default('session_lifetime_hours') == '24'
    assert config.resolve_env_backed_default('session_remember_days') == '30'
    assert config.get_env_backed_int('session_lifetime_hours', settings={}) == 24
