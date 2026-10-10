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


def test_review_provider_round_trips(client):
    assert client.get(BASE).get_json()['reviewProvider']['value'] == 'same_as_pass'

    try:
        r = client.put(f'{BASE}/ad-detection',
                       data=json.dumps({'reviewProvider': 'primary'}),
                       content_type='application/json')
        assert r.status_code == 200, r.get_data(as_text=True)

        after = client.get(BASE).get_json()['reviewProvider']
        assert after['value'] == 'primary'
        assert after['isDefault'] is False
    finally:
        # This module shares one DB singleton with other settings test
        # modules in the same pytest run; leaving review_provider explicit
        # would falsely exempt other stages from provider-change pruning.
        client.put(f'{BASE}/ad-detection',
                   data=json.dumps({'reviewProvider': 'same_as_pass'}),
                   content_type='application/json')


def test_ad_chapter_settings_round_trip(client):
    before = client.get(BASE).get_json()
    assert before['adChaptersEnabled']['value'] is False
    assert before['adChapterCategories']['value']['sponsor'] is False
    assert before['adChapterMinConfidence']['value'] == 0.9

    # recap starts at keep so the adChapterCategories translation (true:
    # keep -> mark) has something to promote (spec 1.4).
    r = client.put(f'{BASE}/ad-detection', data=json.dumps({
        'segmentCategoryActions': {'recap': 'keep'},
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
    assert after['adChapterCategories']['value']['sponsor'] is False
    assert after['adChaptersIncludeHeld']['value'] is True
    assert after['adChapterTitleFormat']['value'] == 'Ad: {category}'
    assert after['adChapterHeldTitleFormat']['value'] == 'Maybe {category}'
    assert after['adChapterResumeTitle']['value'] == 'Back'
    assert after['adChapterMinConfidence']['value'] == 0.5


def test_ad_chapter_categories_false_demotes_mark_leaves_other_actions(client):
    """{sponsor: false} demotes mark to keep and leaves a remove category alone."""
    r = client.put(f'{BASE}/ad-detection', data=json.dumps({
        'segmentCategoryActions': {'sponsor': 'mark', 'cross_promo': 'remove'},
    }), content_type='application/json')
    assert r.status_code == 200, r.get_data(as_text=True)

    r = client.put(f'{BASE}/ad-detection', data=json.dumps({
        'adChapterCategories': {'sponsor': False},
    }), content_type='application/json')
    assert r.status_code == 200, r.get_data(as_text=True)

    after = client.get(BASE).get_json()
    assert after['segmentCategoryActions']['value']['sponsor'] == 'keep'
    assert after['segmentCategoryActions']['value']['cross_promo'] == 'remove'


def test_ad_chapters_enabled_false_demotes_every_mark(client):
    r = client.put(f'{BASE}/ad-detection', data=json.dumps({
        'segmentCategoryActions': {'sponsor': 'mark', 'recap': 'mark'},
    }), content_type='application/json')
    assert r.status_code == 200, r.get_data(as_text=True)

    r = client.put(f'{BASE}/ad-detection', data=json.dumps({
        'adChaptersEnabled': False,
    }), content_type='application/json')
    assert r.status_code == 200, r.get_data(as_text=True)

    after = client.get(BASE).get_json()
    assert after['adChaptersEnabled']['value'] is False
    assert after['segmentCategoryActions']['value']['sponsor'] == 'keep'
    assert after['segmentCategoryActions']['value']['recap'] == 'keep'


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
    assert put({'adChaptersEnabled': 'off'}).status_code == 200
    assert enabled() is False


def test_the_splice_veto_toggle_round_trips(client):
    assert client.get(BASE).get_json()['spliceVetoEnabled']['value'] is True

    for value in (False, True):
        r = client.put(f'{BASE}/ad-detection',
                       data=json.dumps({'spliceVetoEnabled': value}),
                       content_type='application/json')
        assert r.status_code in (200, 204), r.get_data(as_text=True)
        after = client.get(BASE).get_json()['spliceVetoEnabled']
        assert after['value'] is value
        assert after['isDefault'] is False


@pytest.mark.parametrize('value', [None, 'nope', 0])
def test_the_splice_veto_toggle_rejects_non_booleans(client, value):
    before = client.get(BASE).get_json()['spliceVetoEnabled']['value']
    r = client.put(f'{BASE}/ad-detection', data=json.dumps({'spliceVetoEnabled': value}),
                   content_type='application/json')
    assert r.status_code == 400, r.get_data(as_text=True)
    assert client.get(BASE).get_json()['spliceVetoEnabled']['value'] is before


def test_ad_detection_reset_turns_the_splice_veto_back_on(client):
    client.put(f'{BASE}/ad-detection', data=json.dumps({'spliceVetoEnabled': False}),
               content_type='application/json')
    r = client.post(f'{BASE}/ad-detection/reset')
    assert r.status_code == 200, r.get_data(as_text=True)
    after = client.get(BASE).get_json()['spliceVetoEnabled']
    assert after['value'] is True
    assert after['isDefault'] is True


@pytest.mark.parametrize('field,key,default', [
    ('audioReplacementSoundEnabled', 'audio_replacement_sound_enabled', True),
    ('audioMp3StreamCopyEnabled', 'audio_mp3_stream_copy_enabled', False),
])
def test_audio_output_explicit_choice_and_null_reset(client, preserve_setting, field, key, default):
    preserve_setting(key)
    response = client.put(f'{BASE}/ad-detection', json={field: not default})
    assert response.status_code == 200
    assert client.get(BASE).get_json()[field] == {'value': not default, 'isDefault': False}
    response = client.put(f'{BASE}/ad-detection', json={field: None})
    assert response.status_code == 200
    assert client.get(BASE).get_json()[field] == {'value': default, 'isDefault': True}


@pytest.mark.parametrize('field', ['audioReplacementSoundEnabled', 'audioMp3StreamCopyEnabled'])
@pytest.mark.parametrize('invalid', ['false', 0, [], {}])
def test_audio_output_invalid_value_is_atomic(client, preserve_setting, field, invalid):
    preserve_setting('audio_bitrate')
    before = client.get(BASE).get_json()['audioBitrate']
    response = client.put(f'{BASE}/ad-detection', json={'audioBitrate': '256k', field: invalid})
    assert response.status_code == 400
    assert field in response.get_json()['error']
    assert client.get(BASE).get_json()['audioBitrate'] == before


@pytest.mark.parametrize('feed_type', ['subscribed', 'local'])
def test_audio_feed_api_null_inherits_and_false_is_retained(client, temp_db, feed_type):
    source = 'local://example-feed' if feed_type == 'local' else 'https://example.com/feed.xml'
    temp_db.create_podcast('example-feed', source, feed_type=feed_type)
    route = '/api/v1/feeds/example-feed'
    response = client.patch(route, json={'audioReplacementSoundOverride': False, 'audioMp3StreamCopyOverride': True})
    assert response.status_code == 200
    body = client.get(route).get_json()
    assert body['audioReplacementSoundOverride'] is False
    assert body['audioMp3StreamCopyOverride'] is True
    invalid = client.patch(route, json={'audioReplacementSoundOverride': 'false', 'audioMp3StreamCopyOverride': False})
    assert invalid.status_code == 400
    assert temp_db.resolve_audio_output('example-feed')['mp3_stream_copy_enabled'] is True
    response = client.patch(route, json={'audioReplacementSoundOverride': None, 'audioMp3StreamCopyOverride': None})
    assert response.status_code == 200
    assert temp_db.resolve_audio_output('example-feed') == {
        'replacement_sound_enabled': True, 'mp3_stream_copy_enabled': False,
    }
