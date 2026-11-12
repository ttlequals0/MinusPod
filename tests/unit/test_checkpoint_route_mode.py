import pytest

from tests.app_bootstrap import bootstrap

bootstrap('checkpoint_route_')

from main_app import app  # noqa: E402
from tests.integration.openapi_conformance import assert_response_conforms  # noqa: E402


@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c


def _post(client, body=None):
    return client.post('/api/v1/system/database/checkpoint', json=body)


def test_truncate_mode_is_accepted(client):
    resp = _post(client, {'mode': 'truncate'})
    assert resp.status_code in (200, 409)
    assert resp.get_json()['mode'] == 'truncate'


def test_default_mode_is_passive(client):
    assert _post(client).get_json()['mode'] == 'passive'


@pytest.mark.parametrize('body', [{'mode': 'bogus'}, {'mode': ['truncate']}, {'mode': 1}])
def test_bad_mode_is_rejected(client, body):
    assert _post(client, body).status_code == 400


@pytest.mark.parametrize('body', [['truncate'], 'truncate'])
def test_non_object_body_falls_back_to_passive(client, body):
    resp = _post(client, body)
    assert resp.status_code in (200, 409)
    assert resp.get_json()['mode'] == 'passive'


PATH = '/system/database/checkpoint'


@pytest.mark.parametrize('body', [None, {'mode': 'truncate'}])
def test_success_response_matches_the_schema(client, body):
    resp = _post(client, body)
    assert_response_conforms('post', PATH, resp, status=200)


def test_bad_mode_response_matches_the_schema(client):
    assert_response_conforms('post', PATH, _post(client, {'mode': 'bogus'}), status=400)


def test_busy_response_matches_the_schema(client, monkeypatch):
    import api.system as system
    busy = {'mode': 'passive', 'busy': True, 'logPages': 3, 'checkpointedPages': 1, 'durationMs': 0.5}
    monkeypatch.setattr(system.get_database(), 'checkpoint_wal', lambda mode='passive': busy)
    assert_response_conforms('post', PATH, _post(client), status=409)


def test_a_sqlite_failure_returns_503_matching_the_schema(client, monkeypatch):
    import sqlite3
    import api.system as system

    def boom(mode='passive'):
        raise sqlite3.OperationalError('database is locked')

    monkeypatch.setattr(system.get_database(), 'checkpoint_wal', boom)
    assert_response_conforms('post', PATH, _post(client), status=503)
