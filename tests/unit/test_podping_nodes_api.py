"""Podping RPC endpoint configuration API."""
import json

import pytest

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('podping_nodes_api_test_')

from api import get_database
from main_app import app
from podping_listener import PODPING_NODE_SETTING, PODPING_NODES


@pytest.fixture
def db():
    database = get_database()
    saved = {
        key: database.get_setting(key)
        for key in (PODPING_NODE_SETTING, 'app_password')
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


def test_get_returns_active_and_default_nodes(client, db):
    db.set_setting(PODPING_NODE_SETTING, json.dumps(['https://rpc.example']))

    response = client.get('/api/v1/podping/nodes')

    assert response.status_code == 200
    assert response.get_json() == {
        'nodes': ['https://rpc.example'],
        'defaults': PODPING_NODES,
    }


def test_put_normalizes_and_deduplicates_nodes(client, db):
    response = client.put(
        '/api/v1/podping/nodes',
        json={'nodes': [' HTTPS://RPC.EXAMPLE/ ', 'https://rpc.example']},
        headers=_csrf_headers(client),
    )

    assert response.status_code == 200
    assert response.get_json()['nodes'] == ['https://rpc.example']
    assert json.loads(db.get_setting(PODPING_NODE_SETTING)) == ['https://rpc.example']


@pytest.mark.parametrize('nodes', [
    [],
    ['ftp://rpc.example'],
    ['https://user:pass@rpc.example'],
    ['https://@rpc.example'],
    ['https://rpc.example?token=x'],
    ['https://rpc.example#frag'],
    ['https://rpc host.example'],
    ['https://rpc\nhost.example'],
    ['https://rpc.example'] * 21,
])
def test_put_rejects_invalid_node_lists_without_persisting(client, db, nodes):
    db.set_setting(PODPING_NODE_SETTING, json.dumps(['https://old.example']))

    response = client.put(
        '/api/v1/podping/nodes', json={'nodes': nodes},
        headers=_csrf_headers(client),
    )

    assert response.status_code == 400
    assert json.loads(db.get_setting(PODPING_NODE_SETTING)) == ['https://old.example']


def test_put_requires_csrf(client):
    assert client.put('/api/v1/podping/nodes', json={'nodes': ['https://rpc.example']}).status_code == 403


def test_reset_uses_shipped_defaults(client, db):
    db.set_setting(PODPING_NODE_SETTING, json.dumps(['https://custom.example']))

    response = client.post(
        '/api/v1/podping/nodes/reset', headers=_csrf_headers(client))

    assert response.status_code == 200
    assert response.get_json()['nodes'] == PODPING_NODES
    assert db.get_setting(PODPING_NODE_SETTING) is None
