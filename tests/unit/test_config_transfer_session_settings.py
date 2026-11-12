from tests.app_bootstrap import bootstrap
_test_data_dir = bootstrap('session_transfer_test_')
import config_transfer


def test_session_keys_are_portable():
    assert 'session_lifetime_hours' in config_transfer.PORTABLE_SETTINGS
    assert 'session_remember_days' in config_transfer.PORTABLE_SETTINGS
