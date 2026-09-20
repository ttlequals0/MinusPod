"""Manual Podping node check API."""
import json
from datetime import timedelta

import pytest

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('podping_check_api_test_')

from api import get_database
from main_app import app
from podping_listener import MONITOR_HEARTBEAT_SETTING, NODE_CHECK_SETTING
from utils.time import utc_now, utc_now_iso


@pytest.fixture
def db():
    database = get_database()
    saved = {
        key: database.get_setting(key)
        for key in (MONITOR_HEARTBEAT_SETTING, NODE_CHECK_SETTING, 'app_password')
    }
    yield database
    for key, value in saved.items():
        if value is None:
            database.clear_setting(key)
        else:
            database.set_setting(key, value)


@pytest.fixture
def client(db):
    app.config['TESTING'] = True
    db.set_setting('app_password', 'pbkdf2:sha256:fake')
    with app.test_client() as test_client:
        with test_client.session_transaction() as session:
            session['authenticated'] = True
            session['auth_generation'] = db.get_setting_int(
                'auth_session_generation', 0)
        test_client.get('/api/v1/system/status')
        yield test_client


def _csrf_headers(client):
    return {'X-CSRF-Token': client.get_cookie('minuspod_csrf').value}


def _heartbeat(at):
    return json.dumps({'owner': 'test-leader', 'observedAt': at})


def test_check_requires_authentication_and_csrf(client, db):
    db.set_setting(MONITOR_HEARTBEAT_SETTING, _heartbeat(utc_now_iso()))
    with app.test_client() as anonymous:
        assert anonymous.post('/api/v1/system/podping/check').status_code == 401
    assert client.post('/api/v1/system/podping/check').status_code == 403


def test_check_rejects_missing_leader_heartbeat(client, db):
    db.set_setting(
        MONITOR_HEARTBEAT_SETTING,
        _heartbeat((utc_now() - timedelta(minutes=2)).isoformat()),
    )
    response = client.post(
        '/api/v1/system/podping/check', headers=_csrf_headers(client))

    assert response.status_code == 503
    assert response.get_json()['error'] == 'Podping monitor is not available'


def test_check_atomically_reuses_pending_request(client, db):
    db.set_setting(MONITOR_HEARTBEAT_SETTING, _heartbeat(utc_now_iso()))
    first = client.post(
        '/api/v1/system/podping/check', headers=_csrf_headers(client))
    second = client.post(
        '/api/v1/system/podping/check', headers=_csrf_headers(client))

    assert first.status_code == second.status_code == 202
    assert first.get_json()['accepted'] is True
    assert second.get_json()['accepted'] is False
    assert second.get_json()['checkId'] == first.get_json()['checkId']
    assert json.loads(db.get_setting(NODE_CHECK_SETTING))['status'] == 'pending'


def test_check_hides_running_claim_metadata(client, db):
    db.set_setting(MONITOR_HEARTBEAT_SETTING, _heartbeat(utc_now_iso()))
    db.set_setting(NODE_CHECK_SETTING, json.dumps({
        'checkId': 'check-running',
        'status': 'running',
        'requestedAt': '2026-09-20T12:00:00Z',
        'startedAt': '2026-09-20T12:00:01Z',
        'completedAt': None,
        'claimId': 'internal-owner',
        'leaseUntil': '2026-09-20T12:01:01Z',
    }))

    payload = client.post(
        '/api/v1/system/podping/check', headers=_csrf_headers(client)).get_json()

    assert payload['accepted'] is False
    assert payload['status'] == 'running'
    assert 'claimId' not in payload
    assert 'leaseUntil' not in payload


def test_system_status_exposes_completed_check_counts(client, db):
    db.set_setting(NODE_CHECK_SETTING, json.dumps({
        'checkId': 'check-3',
        'status': 'completed',
        'requestedAt': '2026-09-20T12:00:00Z',
        'startedAt': '2026-09-20T12:00:01Z',
        'completedAt': '2026-09-20T12:00:02Z',
        'healthyNodes': 3,
        'totalNodes': 4,
    }))

    check = client.get('/api/v1/system/status').get_json()['podping']['check']

    assert check['status'] == 'completed'
    assert check['healthyNodes'] == 3
    assert check['totalNodes'] == 4
