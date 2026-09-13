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


def test_ad_chapter_settings_round_trip(client):
    before = client.get(BASE).get_json()
    assert before['adChaptersEnabled']['value'] is False
    assert before['adChapterCategories']['value']['sponsor'] is True
    assert before['adChapterMinConfidence']['value'] == 0.9

    r = client.put(f'{BASE}/ad-detection', data=json.dumps({
        'adChaptersEnabled': True,
        'adChapterCategories': {'recap': True},
        'adChaptersIncludeHeld': True,
        'adChapterTitleFormat': 'Ad: {category}',
        'adChapterHeldTitleFormat': 'Maybe {category}',
        'adChapterResumeTitle': 'Back',
        'adChapterMinConfidence': 0.5,
    }), content_type='application/json')
    assert r.status_code == 200, r.get_data(as_text=True)

    after = client.get(BASE).get_json()
    assert after['adChaptersEnabled']['value'] is True
    assert after['adChapterCategories']['value']['recap'] is True
    assert after['adChapterCategories']['value']['sponsor'] is True
    assert after['adChaptersIncludeHeld']['value'] is True
    assert after['adChapterTitleFormat']['value'] == 'Ad: {category}'
    assert after['adChapterHeldTitleFormat']['value'] == 'Maybe {category}'
    assert after['adChapterResumeTitle']['value'] == 'Back'
    assert after['adChapterMinConfidence']['value'] == 0.5


@pytest.mark.parametrize('payload', [
    {'adChapterTitleFormat': '{nope}'},
    {'adChapterTitleFormat': '{category.__class__}'},
    {'adChapterTitleFormat': '{category[0]}'},
    {'adChapterHeldTitleFormat': 42},
    {'adChapterMinConfidence': 1.5},
    {'adChapterMinConfidence': 'high'},
    {'adChapterCategories': {'bogus': True}},
    {'adChapterCategories': {'sponsor': 'yes'}},
    {'adChapterCategories': []},
])
def test_ad_chapter_settings_validation(client, payload):
    r = client.put(f'{BASE}/ad-detection', data=json.dumps(payload),
                   content_type='application/json')
    assert r.status_code == 400, r.get_data(as_text=True)


def test_blank_ad_chapter_titles_reset_to_the_default(client):
    r = client.put(f'{BASE}/ad-detection', data=json.dumps({
        'adChapterTitleFormat': 'Ad: {category}',
        'adChapterHeldTitleFormat': 'Maybe {category}',
        'adChapterResumeTitle': 'Back',
    }), content_type='application/json')
    assert r.status_code == 200, r.get_data(as_text=True)
    assert client.get(BASE).get_json()['adChapterTitleFormat']['isDefault'] is False

    r = client.put(f'{BASE}/ad-detection', data=json.dumps({
        'adChapterTitleFormat': '',
        'adChapterHeldTitleFormat': '   ',
        'adChapterResumeTitle': '',
    }), content_type='application/json')
    assert r.status_code == 200, r.get_data(as_text=True)

    after = client.get(BASE).get_json()
    for key, default in (('adChapterTitleFormat', 'Ad: {label}'),
                         ('adChapterHeldTitleFormat', 'Possible ad: {label}'),
                         ('adChapterResumeTitle', 'Show')):
        assert after[key]['value'] == default
        assert after[key]['isDefault'] is True


def test_ad_chapter_settings_reject_without_partial_write(client):
    def put(payload):
        return client.put(BASE + '/ad-detection', data=json.dumps(payload),
                          content_type='application/json')

    def enabled():
        return client.get(BASE).get_json()['adChaptersEnabled']['value']

    assert put({'adChaptersEnabled': False}).status_code == 200
    assert put({'adChaptersEnabled': True, 'adChapterTitleFormat': '{nope}'}).status_code == 400
    assert enabled() is False
    assert put({'adChaptersEnabled': 'false'}).status_code == 200
    assert enabled() is False
