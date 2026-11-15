"""The hidden precise_cut_edges setting: default off, GET/PUT round trip, reset, resolver."""
import json

import pytest

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('precise_cut_edges_test_', passphrase='precise-cut-edges-passphrase')

import database  # noqa: E402
from config import resolve_precise_cut_edges  # noqa: E402
from database.settings import SETTINGS_REGISTRY, registry_get_default  # noqa: E402
from main_app import app  # noqa: E402


@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c


@pytest.fixture
def db():
    db = database.Database()
    db.reset_setting('precise_cut_edges')
    yield db
    db.reset_setting('precise_cut_edges')


def _get(client):
    resp = client.get('/api/v1/settings')
    assert resp.status_code == 200
    return json.loads(resp.data)


def _put(client, value):
    return client.put('/api/v1/settings/ad-detection', data=json.dumps({'preciseCutEdges': value}),
                      content_type='application/json')


def test_registry_default_is_off():
    spec = SETTINGS_REGISTRY['precise_cut_edges']
    assert spec.payload_key == 'preciseCutEdges'
    assert registry_get_default('precise_cut_edges') is False


def test_get_exposes_default_and_value(client, db):
    data = _get(client)
    assert data['defaults']['preciseCutEdges'] is False
    assert data['preciseCutEdges'] == {'value': False, 'isDefault': True}


def test_put_round_trip_and_reset(client, db):
    assert _put(client, True).status_code == 200
    assert db.get_setting('precise_cut_edges') == 'true'
    assert _get(client)['preciseCutEdges'] == {'value': True, 'isDefault': False}
    assert resolve_precise_cut_edges(db) is True
    assert db.reset_setting('precise_cut_edges') is True
    assert resolve_precise_cut_edges(db) is False
    assert _get(client)['preciseCutEdges']['value'] is False


def test_put_rejects_a_non_boolean(client, db):
    assert _put(client, 'yes').status_code == 400
    assert resolve_precise_cut_edges(db) is False


def test_resolver_fails_closed():
    class Broken:
        def get_setting(self, key):
            raise RuntimeError('db down')
    assert resolve_precise_cut_edges(Broken()) is False
    assert resolve_precise_cut_edges(None) is False
