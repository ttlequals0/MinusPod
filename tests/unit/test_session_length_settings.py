from tests.app_bootstrap import bootstrap
_test_data_dir = bootstrap('session_len_test_')
import json
import pytest
import config
from database import Database
from main_app import app

BASE = '/api/v1/settings'


def _vd(entry):
    """An entry without its additive limits."""
    return {k: entry[k] for k in ('value', 'isDefault')}
PUT = f'{BASE}/ad-detection'


@pytest.fixture
def client():
    app.config['TESTING'] = True
    db = Database()
    db.set_setting('app_password', '')
    db.set_setting('session_lifetime_hours', '', is_default=True)
    db.set_setting('session_remember_days', '', is_default=True)
    with app.test_client() as c:
        yield c


def _put(client, payload):
    return client.put(PUT, data=json.dumps(payload), content_type='application/json')


def test_defaults_when_unset(client):
    body = client.get(BASE).get_json()
    assert _vd(body['sessionLifetimeHours']) == {'value': 24, 'isDefault': True}
    assert _vd(body['sessionRememberDays']) == {'value': 30, 'isDefault': True}


def test_env_seeds_default(monkeypatch):
    monkeypatch.setenv('SESSION_LIFETIME_HOURS', '72')
    assert config.resolve_env_backed_default('session_lifetime_hours') == '72'


def test_invalid_env_falls_back_to_default(monkeypatch):
    monkeypatch.setenv('SESSION_REMEMBER_DAYS', '0')
    assert config.resolve_env_backed_default('session_remember_days') == '30'


def test_env_value_applies_without_saved_row(client, monkeypatch):
    monkeypatch.setenv('SESSION_LIFETIME_HOURS', '72')
    assert client.get(BASE).get_json()['sessionLifetimeHours']['value'] == 72


def test_saved_value_wins_over_env(client, monkeypatch):
    monkeypatch.setenv('SESSION_LIFETIME_HOURS', '72')
    r = _put(client, {'sessionLifetimeHours': 100})
    assert r.status_code == 200, r.get_data(as_text=True)
    got = client.get(BASE).get_json()['sessionLifetimeHours']
    assert _vd(got) == {'value': 100, 'isDefault': False}


def test_numeric_string_is_accepted_like_other_int_settings(client):
    assert _put(client, {'sessionLifetimeHours': '48'}).status_code == 200
    assert client.get(BASE).get_json()['sessionLifetimeHours']['value'] == 48


def test_stored_numeric_string_is_read_back(client):
    Database().set_setting('session_remember_days', '45', is_default=False)
    assert client.get(BASE).get_json()['sessionRememberDays']['value'] == 45


def test_boundaries_accepted(client):
    r = _put(client, {'sessionLifetimeHours': 8760, 'sessionRememberDays': 1})
    assert r.status_code == 200
    body = client.get(BASE).get_json()
    assert body['sessionLifetimeHours']['value'] == 8760
    assert body['sessionRememberDays']['value'] == 1


@pytest.mark.parametrize('payload', [
    {'sessionLifetimeHours': 8761},
    {'sessionLifetimeHours': 0},
    {'sessionRememberDays': 0},
    {'sessionRememberDays': 366},
    {'sessionLifetimeHours': 'soon'},
    {'sessionLifetimeHours': 24.9},
    {'sessionLifetimeHours': 8760.9},
    {'sessionRememberDays': 1.5},
    {'sessionLifetimeHours': '24.9'},
    {'sessionLifetimeHours': float('inf')},
    {'sessionLifetimeHours': [24]},
    {'sessionLifetimeHours': True},
])
def test_put_rejects_bad_values(client, payload):
    assert _put(client, payload).status_code == 400


def test_rejected_payload_persists_nothing(client):
    r = _put(client, {'sessionLifetimeHours': 10, 'sessionRememberDays': 0})
    assert r.status_code == 400
    assert client.get(BASE).get_json()['sessionLifetimeHours']['isDefault'] is True


def test_reset_returns_to_default(client):
    _put(client, {'sessionRememberDays': 90})
    assert Database().reset_setting('session_remember_days') is True
    assert _vd(client.get(BASE).get_json()['sessionRememberDays']) == {'value': 30, 'isDefault': True}


def test_bulk_ad_reset_leaves_session_settings(client):
    _put(client, {'sessionRememberDays': 90})
    client.post(f'{BASE}/ad-detection/reset')
    assert client.get(BASE).get_json()['sessionRememberDays']['value'] == 90


def test_whole_number_float_is_accepted(client):
    assert _put(client, {'sessionLifetimeHours': 24.0}).status_code == 200
    assert client.get(BASE).get_json()['sessionLifetimeHours']['value'] == 24


@pytest.mark.parametrize('field,key,saved,default', [
    ('sessionLifetimeHours', 'session_lifetime_hours', 100, 24),
    ('sessionRememberDays', 'session_remember_days', 90, 30),
])
def test_null_resets_to_the_default(client, field, key, saved, default):
    assert _put(client, {field: saved}).status_code == 200
    assert _vd(client.get(BASE).get_json()[field]) == {'value': saved, 'isDefault': False}
    assert _put(client, {field: None}).status_code == 200
    assert _vd(client.get(BASE).get_json()[field]) == {'value': default, 'isDefault': True}


def test_null_reset_shows_the_env_value_again(client, monkeypatch):
    monkeypatch.setenv('SESSION_LIFETIME_HOURS', '72')
    _put(client, {'sessionLifetimeHours': 100})
    assert _put(client, {'sessionLifetimeHours': None}).status_code == 200
    assert _vd(client.get(BASE).get_json()['sessionLifetimeHours']) == {'value': 72, 'isDefault': True}


def test_null_beside_a_bad_value_changes_nothing(client):
    _put(client, {'sessionLifetimeHours': 100})
    assert _put(client, {'sessionLifetimeHours': None, 'sessionRememberDays': 0}).status_code == 400
    assert client.get(BASE).get_json()['sessionLifetimeHours']['value'] == 100


def test_get_reports_the_limits_beside_each_value(client):
    body = client.get(BASE).get_json()
    assert (body['sessionLifetimeHours']['min'], body['sessionLifetimeHours']['max']) == (1, 8760)
    assert (body['sessionRememberDays']['min'], body['sessionRememberDays']['max']) == (1, 365)
    assert 'step' not in body['sessionLifetimeHours']
