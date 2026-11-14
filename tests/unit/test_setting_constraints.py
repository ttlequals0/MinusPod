"""SettingConstraint: one declaration feeds payload checks, stored checks and GET limits."""
import pytest

import config
from config import SETTING_CONSTRAINTS as C
from database.settings import SETTINGS_REGISTRY


def test_every_constraint_is_on_a_registry_spec_with_a_validator():
    for key, constraint in C.items():
        spec = SETTINGS_REGISTRY[key]
        assert spec.constraint is constraint
        assert spec.validator == constraint.stored_valid


@pytest.mark.parametrize('raw,ok', [(1, True), (8760, True), (24.0, True), ('48', True), (0, False),
                                    (8761, False), (1.5, False), (True, False), ('x', False)])
def test_int_payload(raw, ok):
    _value, issue = C['session_lifetime_hours'].payload_issue('sessionLifetimeHours', raw)
    assert (issue is None) is ok


def test_int_messages_are_unchanged():
    c = C['session_remember_days']
    assert c.payload_issue('f', 'x')[1] == 'f must be an integer'
    assert c.payload_issue('f', 0)[1] == 'f must be between 1 and 365'


@pytest.mark.parametrize('raw,ok', [(0.65, True), (0.95, True), (1, False), ('0.7', False),
                                    (True, False), (float('nan'), False), (10 ** 400, False)])
def test_float_payload(raw, ok):
    _value, issue = C['fingerprint_match_strictness'].payload_issue('f', raw)
    assert (issue is None) is ok
    if not ok:
        assert issue == 'f must be a number between 0.65 and 0.95'


def test_bool_and_choice_messages():
    assert C['fingerprint_enabled'].payload_issue('f', 1)[1] == 'f must be true or false'
    assert C['queue_order'].payload_issue('f', None)[1] == 'f must be one of: queued, oldest_first, newest_first'


def test_only_the_session_settings_reset_on_null():
    assert {k for k, c in C.items() if c.null_resets} == {'session_lifetime_hours', 'session_remember_days'}


def test_stored_checks():
    assert C['session_lifetime_hours'].stored_valid('24')
    assert not C['session_lifetime_hours'].stored_valid('24.0')
    assert not C['fingerprint_min_coverage'].stored_valid('nan')
    assert C['fingerprint_enabled'].stored_valid('false') and not C['fingerprint_enabled'].stored_valid('0')


def test_api_fields():
    assert C['fingerprint_min_ad_length_s'].api_fields() == {'min': 3.0, 'max': 30.0, 'step': 1.0}
    assert C['session_remember_days'].api_fields() == {'min': 1, 'max': 365}
    assert C['queue_order'].api_fields() == {'options': list(config.QUEUE_ORDERS)}
    assert C['fingerprint_enabled'].api_fields() == {}


def test_env_backed_int_clamps_to_the_constraint(monkeypatch):
    monkeypatch.setattr(config, '_db_setting', lambda key: '99999')
    assert config.get_env_backed_int('session_lifetime_hours') == 8760
