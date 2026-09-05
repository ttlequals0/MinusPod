"""API tests for the notification timezone setting (Task 7, unified search plan)."""
import json

import pytest

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('notification_timezone_test_')
from main_app import app

BASE = '/api/v1/settings/notifications/timezone'


@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c


def _put(client, timezone):
    return client.put(BASE, data=json.dumps({'timezone': timezone}), content_type='application/json')


class TestGetNotificationTimezone:
    def test_defaults_to_utc(self, client):
        response = client.get(BASE)
        assert response.status_code == 200
        assert json.loads(response.data)['timezone'] == 'UTC'


class TestPutNotificationTimezone:
    def test_known_zone_is_accepted_and_persists(self, client):
        response = _put(client, 'America/New_York')
        assert response.status_code == 200, response.data
        assert json.loads(response.data)['timezone'] == 'America/New_York'
        assert json.loads(client.get(BASE).data)['timezone'] == 'America/New_York'

    def test_unknown_zone_is_rejected_with_400(self, client):
        response = _put(client, 'Not/AZone')
        assert response.status_code == 400
        assert 'Not/AZone' in json.loads(response.data)['error']

    def test_missing_timezone_is_rejected(self, client):
        response = client.put(BASE, data=json.dumps({}), content_type='application/json')
        assert response.status_code == 400

    def test_non_string_timezone_is_rejected(self, client):
        response = client.put(BASE, data=json.dumps({'timezone': 5}), content_type='application/json')
        assert response.status_code == 400
