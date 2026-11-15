"""heldWidenSeconds through GET /settings and PUT /settings/ad-detection."""
import json

import pytest

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('held_widen_api_test_', passphrase='held-widen-api-test-passphrase')
from main_app import app


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv('HELD_WIDEN_SECONDS', raising=False)
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c
        c.put('/api/v1/settings/ad-detection',
              data=json.dumps({'heldWidenSeconds': None}), content_type='application/json')


def _put(client, payload):
    return client.put('/api/v1/settings/ad-detection',
                      data=json.dumps(payload), content_type='application/json')


def _entry(client):
    return client.get('/api/v1/settings').get_json()['stageTunables']['heldWidenSeconds']


def test_default_is_zero_and_listed_in_the_defaults_block(client):
    body = client.get('/api/v1/settings').get_json()
    assert body['stageTunableDefaults']['heldWidenSeconds'] == 0.0
    assert _entry(client) == {'value': 0.0, 'isDefault': True, 'envOverride': None}


def test_round_trips_a_fractional_value(client):
    assert _put(client, {'heldWidenSeconds': 45.5}).status_code == 200
    entry = _entry(client)
    assert entry['value'] == 45.5
    assert entry['isDefault'] is False


def test_null_clears_to_the_default(client):
    _put(client, {'heldWidenSeconds': 60})
    assert _put(client, {'heldWidenSeconds': None}).status_code == 200
    assert _entry(client)['value'] == 0.0


@pytest.mark.parametrize('bad', [-1, 120.5, 'wide'])
def test_out_of_range_or_non_numeric_is_rejected(client, bad):
    r = _put(client, {'heldWidenSeconds': bad})
    assert r.status_code == 400
    assert 'heldWidenSeconds' in r.get_json()['error']


def test_ad_detection_reset_clears_it(client):
    _put(client, {'heldWidenSeconds': 60})
    assert client.post('/api/v1/settings/ad-detection/reset').status_code == 200
    assert _entry(client)['value'] == 0.0
