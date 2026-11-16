"""heldWidenSeconds through GET /settings and PUT /settings/ad-detection."""
import json

import pytest

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('held_widen_api_test_', passphrase='held-widen-api-test-passphrase')
from main_app import app


@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c
        c.post('/api/v1/settings/ad-detection/reset')


def _put(client, payload):
    return client.put('/api/v1/settings/ad-detection',
                      data=json.dumps(payload), content_type='application/json')


def _entry(client):
    return client.get('/api/v1/settings').get_json()['heldWidenSeconds']


def test_default_is_zero_and_listed_in_the_defaults_block(client):
    body = client.get('/api/v1/settings').get_json()
    assert body['defaults']['heldWidenSeconds'] == 0.0
    assert _entry(client)['value'] == 0.0
    assert 'heldWidenSeconds' not in body['stageTunables']


def test_round_trips_a_fractional_value(client):
    assert _put(client, {'heldWidenSeconds': 45.5}).status_code == 200
    assert _entry(client)['value'] == 45.5


def test_ceiling_and_floor_are_accepted(client):
    assert _put(client, {'heldWidenSeconds': 120}).status_code == 200
    assert _entry(client)['value'] == 120.0
    assert _put(client, {'heldWidenSeconds': 0}).status_code == 200
    assert _entry(client)['value'] == 0.0


@pytest.mark.parametrize('bad', [-1, 120.5, 'wide', None])
def test_out_of_range_or_non_numeric_is_rejected(client, bad):
    r = _put(client, {'heldWidenSeconds': bad})
    assert r.status_code == 400
    assert 'heldWidenSeconds' in r.get_json()['error']


def test_ad_detection_reset_clears_it(client):
    _put(client, {'heldWidenSeconds': 60})
    assert client.post('/api/v1/settings/ad-detection/reset').status_code == 200
    assert _entry(client)['value'] == 0.0
