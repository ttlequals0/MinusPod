"""Global adChaptersEnabled/adChapterCategories compatibility fields.

Retired in favor of the 'mark' segment action (2.98.0,
mark_action_from_ad_chapters_v1): GET /settings derives them from
segment_category_actions; PUT/PATCH /settings/ad-detection translates them
back into it. See spec 1.4 and review focus 3.
"""
import json

import pytest

from tests.app_bootstrap import bootstrap

bootstrap('settings_mark_compat_test_', passphrase='settings-mark-compat-test-pass')

from main_app import app  # noqa: E402

BASE = '/api/v1/settings'


@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c


@pytest.fixture(autouse=True)
def _reset_segment_category_actions():
    """This module shares one DB singleton with other settings test modules
    in the same pytest run (see test_settings_registry_api_roundtrip.py);
    clear the mutated map so a later module's "fresh default" assertions
    are not left looking at a category this file marked."""
    yield
    from api import get_database
    get_database().clear_setting('segment_category_actions')


def _put(client, payload):
    return client.put(f'{BASE}/ad-detection', data=json.dumps(payload),
                       content_type='application/json')


def test_get_derives_disabled_by_default(client):
    body = client.get(BASE).get_json()
    assert body['adChaptersEnabled']['value'] is False
    assert all(v is False for v in body['adChapterCategories']['value'].values())


def test_adchaptercategories_true_marks_a_keep_category(client):
    assert _put(client, {'segmentCategoryActions': {'sponsor': 'keep'}}).status_code == 200
    assert _put(client, {'adChapterCategories': {'sponsor': True}}).status_code == 200

    body = client.get(BASE).get_json()
    assert body['segmentCategoryActions']['value']['sponsor'] == 'mark'
    assert body['adChapterCategories']['value']['sponsor'] is True
    assert body['adChaptersEnabled']['value'] is True


def test_adchaptercategories_false_demotes_mark_to_keep_review_focus_3(client):
    """Review focus 3: {sponsor: false} demotes mark to keep and leaves a
    remove category alone."""
    assert _put(client, {'segmentCategoryActions': {
        'sponsor': 'mark', 'cross_promo': 'remove'}}).status_code == 200
    assert _put(client, {'adChapterCategories': {'sponsor': False}}).status_code == 200

    body = client.get(BASE).get_json()
    assert body['segmentCategoryActions']['value']['sponsor'] == 'keep'
    assert body['segmentCategoryActions']['value']['cross_promo'] == 'remove'


def test_adchaptercategories_false_on_a_non_mark_category_is_a_no_op(client):
    assert _put(client, {'segmentCategoryActions': {'sponsor': 'remove'}}).status_code == 200
    assert _put(client, {'adChapterCategories': {'sponsor': False}}).status_code == 200

    body = client.get(BASE).get_json()
    assert body['segmentCategoryActions']['value']['sponsor'] == 'remove'


def test_adchapterscategories_mixed_true_false_untouched(client):
    assert _put(client, {'segmentCategoryActions': {
        'sponsor': 'keep', 'cross_promo': 'mark', 'recap': 'remove'}}).status_code == 200
    assert _put(client, {'adChapterCategories': {
        'sponsor': True, 'cross_promo': False, 'recap': False}}).status_code == 200

    body = client.get(BASE).get_json()['segmentCategoryActions']['value']
    assert body['sponsor'] == 'mark'
    assert body['cross_promo'] == 'keep'
    assert body['recap'] == 'remove'


def test_adchaptersenabled_false_demotes_every_mark(client):
    assert _put(client, {'segmentCategoryActions': {
        'sponsor': 'mark', 'recap': 'mark', 'cross_promo': 'remove'}}).status_code == 200
    assert _put(client, {'adChaptersEnabled': False}).status_code == 200

    body = client.get(BASE).get_json()
    assert body['adChaptersEnabled']['value'] is False
    assert body['segmentCategoryActions']['value']['sponsor'] == 'keep'
    assert body['segmentCategoryActions']['value']['recap'] == 'keep'
    assert body['segmentCategoryActions']['value']['cross_promo'] == 'remove'


def test_adchaptersenabled_true_is_accepted_and_ignored(client):
    assert _put(client, {'segmentCategoryActions': {'sponsor': 'mark'}}).status_code == 200
    assert _put(client, {'adChaptersEnabled': True}).status_code == 200

    body = client.get(BASE).get_json()
    assert body['adChaptersEnabled']['value'] is True
    assert body['segmentCategoryActions']['value']['sponsor'] == 'mark'


@pytest.mark.parametrize('payload', [
    {'adChapterCategories': {'bogus': True}},
    {'adChapterCategories': {'sponsor': 'yes'}},
    {'adChapterCategories': []},
])
def test_adchaptercategories_validates(client, payload):
    assert _put(client, payload).status_code == 400
