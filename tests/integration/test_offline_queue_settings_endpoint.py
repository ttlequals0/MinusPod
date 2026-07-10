"""Integration tests for GET/PUT /settings/offline-queue (#482)."""
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='offline-queue-settings-test-'))


@pytest.fixture
def _clean_settings(app_client):
    from api import get_database
    db = get_database()
    yield db
    db.set_setting('offline_queue_enabled', 'false')
    db.set_setting('offline_queue_ttl_hours', '48')


def _csrf(app_client):
    with app_client.session_transaction() as sess:
        sess['authenticated'] = True
    app_client.get('/api/v1/auth/status')
    cookie = app_client.get_cookie('minuspod_csrf')
    return {'X-CSRF-Token': cookie.value} if cookie else {}


def test_get_defaults(app_client, _clean_settings):
    _csrf(app_client)
    body = app_client.get('/api/v1/settings/offline-queue').get_json()
    assert body['enabled'] is False
    assert body['ttlHours'] == 48
    assert body['deferredCount'] == 0


def test_put_happy_path_and_persistence(app_client, _clean_settings):
    hdr = _csrf(app_client)
    r = app_client.put('/api/v1/settings/offline-queue',
                       json={'enabled': True, 'ttlHours': 12}, headers=hdr)
    assert r.status_code == 200
    body = r.get_json()
    assert body['enabled'] is True
    assert body['ttlHours'] == 12
    roundtrip = app_client.get('/api/v1/settings/offline-queue').get_json()
    assert roundtrip['enabled'] is True
    assert roundtrip['ttlHours'] == 12


@pytest.mark.parametrize('payload', [
    {'ttlHours': 0},
    {'ttlHours': 1000},
    {'ttlHours': '12'},
    {'ttlHours': True},
    {'enabled': 'yes'},
    'enabled',
    ['enabled'],
])
def test_put_validation_failures(app_client, _clean_settings, payload):
    hdr = _csrf(app_client)
    r = app_client.put('/api/v1/settings/offline-queue', json=payload, headers=hdr)
    assert r.status_code == 400


def test_put_partial_update_keeps_other_field(app_client, _clean_settings):
    hdr = _csrf(app_client)
    app_client.put('/api/v1/settings/offline-queue',
                   json={'enabled': True, 'ttlHours': 24}, headers=hdr)
    r = app_client.put('/api/v1/settings/offline-queue',
                       json={'ttlHours': 72}, headers=hdr)
    assert r.status_code == 200
    body = r.get_json()
    assert body['enabled'] is True
    assert body['ttlHours'] == 72


def test_system_queue_includes_deferred_count(app_client, _clean_settings):
    _csrf(app_client)
    body = app_client.get('/api/v1/system/queue').get_json()
    assert 'deferred' in body
    assert body['deferred'] == 0
