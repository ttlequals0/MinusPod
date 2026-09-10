"""Integration tests for GET/PUT /settings/rate-limit-hold (#696)."""
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='rate-limit-hold-settings-test-'))


@pytest.fixture
def _clean_settings(app_client):
    from api import get_database
    db = get_database()
    yield db
    db.set_setting('rate_limit_hold_enabled', 'false')
    db.set_setting('rate_limit_hold_until', '')
    db.set_setting('llm_usage_url', '')
    db.set_setting('rate_limit_probe_minutes', '5')


def _csrf(app_client):
    with app_client.session_transaction() as sess:
        sess['authenticated'] = True
    app_client.get('/api/v1/auth/status')
    cookie = app_client.get_cookie('minuspod_csrf')
    return {'X-CSRF-Token': cookie.value} if cookie else {}


def test_get_defaults(app_client, _clean_settings):
    _csrf(app_client)
    body = app_client.get('/api/v1/settings/rate-limit-hold').get_json()
    assert body == {
        'enabled': False, 'holdUntil': None,
        'llmUsageUrl': '', 'rateLimitProbeMinutes': 5,
    }


def test_put_llm_usage_url_and_probe_minutes(app_client, _clean_settings):
    hdr = _csrf(app_client)
    r = app_client.put('/api/v1/settings/rate-limit-hold', json={
        'llmUsageUrl': 'https://your-proxy:8001/v1/usage',
        'rateLimitProbeMinutes': 10,
    }, headers=hdr)
    assert r.status_code == 200
    body = r.get_json()
    assert body['llmUsageUrl'] == 'https://your-proxy:8001/v1/usage'
    assert body['rateLimitProbeMinutes'] == 10
    roundtrip = app_client.get('/api/v1/settings/rate-limit-hold').get_json()
    assert roundtrip['llmUsageUrl'] == 'https://your-proxy:8001/v1/usage'
    assert roundtrip['rateLimitProbeMinutes'] == 10


def test_put_probe_minutes_zero_is_allowed(app_client, _clean_settings):
    hdr = _csrf(app_client)
    r = app_client.put('/api/v1/settings/rate-limit-hold',
                       json={'rateLimitProbeMinutes': 0}, headers=hdr)
    assert r.status_code == 200
    assert r.get_json()['rateLimitProbeMinutes'] == 0


@pytest.mark.parametrize('value', [-1, 61, 'five', 5.5, True])
def test_put_probe_minutes_out_of_range_rejected(app_client, _clean_settings, value):
    hdr = _csrf(app_client)
    r = app_client.put('/api/v1/settings/rate-limit-hold',
                       json={'rateLimitProbeMinutes': value}, headers=hdr)
    assert r.status_code == 400


def test_put_llm_usage_url_rejects_ssrf_unsafe_url(app_client, _clean_settings):
    hdr = _csrf(app_client)
    r = app_client.put('/api/v1/settings/rate-limit-hold',
                       json={'llmUsageUrl': 'http://169.254.169.254/latest/meta-data'},
                       headers=hdr)
    assert r.status_code == 400


def test_put_llm_usage_url_clears_with_empty_string(app_client, _clean_settings):
    hdr = _csrf(app_client)
    app_client.put('/api/v1/settings/rate-limit-hold',
                   json={'llmUsageUrl': 'https://your-proxy:8001/v1/usage'}, headers=hdr)
    r = app_client.put('/api/v1/settings/rate-limit-hold',
                       json={'llmUsageUrl': ''}, headers=hdr)
    assert r.status_code == 200
    assert r.get_json()['llmUsageUrl'] == ''


def test_put_happy_path_and_persistence(app_client, _clean_settings):
    hdr = _csrf(app_client)
    r = app_client.put('/api/v1/settings/rate-limit-hold',
                       json={'enabled': True}, headers=hdr)
    assert r.status_code == 200
    assert r.get_json()['enabled'] is True
    roundtrip = app_client.get('/api/v1/settings/rate-limit-hold').get_json()
    assert roundtrip['enabled'] is True


def test_get_hides_a_marker_past_its_reset(app_client, _clean_settings):
    _csrf(app_client)
    _clean_settings.set_setting('rate_limit_hold_until', '2020-01-01T00:00:00Z')
    body = app_client.get('/api/v1/settings/rate-limit-hold').get_json()
    assert body['holdUntil'] is None


def test_put_ttl_hours_is_ignored(app_client, _clean_settings):
    hdr = _csrf(app_client)
    r = app_client.put('/api/v1/settings/rate-limit-hold',
                       json={'enabled': True, 'ttlHours': 12}, headers=hdr)
    assert r.status_code == 200
    assert 'ttlHours' not in r.get_json()
    assert _clean_settings.get_setting('rate_limit_hold_ttl_hours') is None


@pytest.mark.parametrize('payload', [
    {'enabled': 'yes'},
    'enabled',
    ['enabled'],
])
def test_put_validation_failures(app_client, _clean_settings, payload):
    hdr = _csrf(app_client)
    r = app_client.put('/api/v1/settings/rate-limit-hold', json=payload, headers=hdr)
    assert r.status_code == 400


def test_put_enabled_false_clears_the_pause_marker(app_client, _clean_settings):
    from unittest.mock import patch
    hdr = _csrf(app_client)
    db = _clean_settings
    db.set_setting('rate_limit_hold_until', '2999-01-01T00:00:00Z')
    db.set_setting('rate_limit_hold_since', '2999-01-01T00:00:00Z')
    with patch('api.settings.fire_queue_resumed_event') as fire:
        r = app_client.put('/api/v1/settings/rate-limit-hold',
                           json={'enabled': False}, headers=hdr)
    assert r.status_code == 200
    assert r.get_json()['holdUntil'] is None
    assert db.get_setting('rate_limit_hold_until') is None
    fire.assert_called_once_with(held_since='2999-01-01T00:00:00Z')


def test_put_enabled_false_without_a_pause_stays_quiet(app_client, _clean_settings):
    from unittest.mock import patch
    hdr = _csrf(app_client)
    with patch('api.settings.fire_queue_resumed_event') as fire:
        r = app_client.put('/api/v1/settings/rate-limit-hold',
                           json={'enabled': False}, headers=hdr)
    assert r.status_code == 200
    fire.assert_not_called()
