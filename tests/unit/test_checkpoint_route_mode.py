import pytest

from tests.app_bootstrap import bootstrap

bootstrap('checkpoint_route_')

from main_app import app  # noqa: E402


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
