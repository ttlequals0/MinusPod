"""GET /settings must reflect saved System One tuneables (#917)."""
import json

import pytest

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('systemone_tunables_api_')
from main_app import app  # noqa: E402


@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess['authenticated'] = True
        c.get('/api/v1/auth/status')
        yield c


def _csrf_headers(client):
    for cookie in client._cookies.values():
        if cookie.key == 'minuspod_csrf':
            return {'X-CSRF-Token': cookie.value}
    return {}


def test_saved_profile_round_trips_through_get(client):
    patch = {'systemOneTunables': {'secondary': {'systemone-compatible': {'maxQuestionsPerRequest': 60}}}}
    r = client.put('/api/v1/settings/ad-detection', data=json.dumps(patch),
                   content_type='application/json', headers=_csrf_headers(client))
    assert r.status_code == 200

    settings = client.get('/api/v1/settings').get_json()
    profile = settings['systemOneTunables']['secondary']['systemone-compatible']
    assert profile['maxQuestionsPerRequest'] == 60
    assert settings['systemOneTunablesIsDefault']['secondary']['systemone-compatible'] is False
    assert settings['systemOneTunablesIsDefault']['primary']['typesafe'] is True
