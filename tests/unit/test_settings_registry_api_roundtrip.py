"""GET /settings and PATCH /settings are assembled from hand-written lists,
not from SETTINGS_REGISTRY, so a key can pass every registry test and still
be invisible to the API and unsettable from the UI. This asserts the two
stay in step.
"""
import json

import pytest

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('settings_roundtrip_test_',
                           passphrase='settings-roundtrip-test-pass')

from database.settings import SETTINGS_REGISTRY  # noqa: E402
from main_app import app  # noqa: E402

BASE = '/api/v1/settings'

# Keys the GET payload deliberately omits. Extend only with a reason.
NOT_IN_GET_PAYLOAD = {
    'api_key',                # secret, never echoed
    'notification_timezone',  # served by GET /settings/notifications/timezone
}


@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c


def test_every_registry_payload_key_is_returned_by_get(client):
    body = client.get(BASE).get_json()
    missing = sorted(
        spec.payload_key for key, spec in SETTINGS_REGISTRY.items()
        if spec.payload_key and key not in NOT_IN_GET_PAYLOAD
        and spec.payload_key not in body
    )
    assert missing == [], (
        f"registry keys absent from GET {BASE}: {missing}. "
        "Add them to the payload dict in src/api/settings.py."
    )


def test_the_keep_override_round_trips(client):
    assert client.get(BASE).get_json()['daiDifferentialOverridesKeep']['value'] is True

    r = client.put(f'{BASE}/ad-detection',
                   data=json.dumps({'daiDifferentialOverridesKeep': False}),
                   content_type='application/json')
    assert r.status_code in (200, 204), r.get_data(as_text=True)

    after = client.get(BASE).get_json()['daiDifferentialOverridesKeep']
    assert after['value'] is False
    assert after['isDefault'] is False
