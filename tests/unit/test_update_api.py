import json
from unittest.mock import patch

import pytest

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('update_api_test_')

from main_app import app  # noqa: E402

STATUS = {'current': {'version': '2.74.0'}, 'stable': None, 'edge': None,
          'channel': 'stable', 'updateAvailable': False}


@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c


class TestUpdatesEndpoint:
    def test_returns_status(self, client):
        with patch('update_checker.get_update_status', return_value=STATUS) as g:
            r = client.get('/api/v1/system/updates')
        assert r.status_code == 200, r.data
        assert r.get_json()['channel'] == 'stable'
        assert g.call_args.kwargs.get('force') is False

    def test_refresh_param_forces(self, client):
        with patch('update_checker.get_update_status', return_value=STATUS) as g:
            client.get('/api/v1/system/updates?refresh=true')
        assert g.call_args.kwargs.get('force') is True

    def test_fetch_failure_is_502(self, client):
        with patch('update_checker.get_update_status',
                   side_effect=ValueError('github down')):
            r = client.get('/api/v1/system/updates')
        assert r.status_code == 502
        assert 'error' in r.get_json()


class TestUpdateCheckSettings:
    def _put(self, client, payload):
        return client.put('/api/v1/settings/update-check',
                          data=json.dumps(payload),
                          content_type='application/json')

    def test_defaults(self, client):
        r = client.get('/api/v1/settings/update-check')
        assert r.get_json() == {'enabled': True, 'channel': 'stable'}

    def test_put_roundtrip(self, client):
        r = self._put(client, {'enabled': False, 'channel': 'edge'})
        assert r.status_code == 200, r.data
        assert r.get_json() == {'enabled': False, 'channel': 'edge'}
        assert client.get('/api/v1/settings/update-check').get_json() == \
            {'enabled': False, 'channel': 'edge'}

    def test_bad_channel_rejected(self, client):
        r = self._put(client, {'channel': 'nightly'})
        assert r.status_code == 400
