"""Tests for segment category action resolution (issue #565 Task 2):
the per-feed -> global -> DEFAULT_SEGMENT_ACTION resolver, global-setting
PUT validation, the GET /settings defaults block, and per-feed PATCH
validation.

Resolver under test (src/database/podcasts.py):
- resolve_segment_actions(slug, podcast=None)
"""
import json

import pytest

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap(
    'segment_actions_test_', passphrase='segment-actions-test-passphrase')

import database
from config import SEGMENT_CATEGORIES, DEFAULT_SEGMENT_ACTION
from main_app import app

ALL_REMOVE = {cat: DEFAULT_SEGMENT_ACTION for cat in SEGMENT_CATEGORIES}


@pytest.fixture
def client():
    """Flask test client."""
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c


@pytest.fixture
def db():
    return database.Database()


@pytest.fixture
def feed_slug(db):
    slug = 'segment-actions-test-feed'
    db.create_podcast(slug, 'https://example.com/feed.xml', title='Segment Actions Test')
    yield slug
    db.delete_podcast(slug)


def _get_settings(client):
    resp = client.get('/api/v1/settings')
    assert resp.status_code == 200
    return json.loads(resp.data)


class TestResolverDefaults:
    def test_all_remove_when_nothing_set(self, db, feed_slug):
        assert db.resolve_segment_actions(feed_slug) == ALL_REMOVE

    def test_global_map_honored(self, db, feed_slug):
        db.set_setting(
            'segment_category_actions',
            json.dumps({'intro': 'keep', 'outro': 'beep'}),
            is_default=False)
        expected = dict(ALL_REMOVE, intro='keep', outro='beep')
        assert db.resolve_segment_actions(feed_slug) == expected

    def test_per_feed_overrides_beat_global_per_category(self, db, feed_slug):
        db.set_setting(
            'segment_category_actions',
            json.dumps({'intro': 'keep', 'outro': 'beep'}),
            is_default=False)
        db.update_podcast(
            feed_slug, segment_category_actions=json.dumps({'intro': 'beep'}))
        # intro comes from the per-feed override; outro still falls through
        # to the global map (the per-feed override is partial, not a
        # whole-map replacement).
        expected = dict(ALL_REMOVE, intro='beep', outro='beep')
        assert db.resolve_segment_actions(feed_slug) == expected

    def test_malformed_global_json_ignored(self, db, feed_slug):
        db.set_setting('segment_category_actions', 'not-json', is_default=False)
        assert db.resolve_segment_actions(feed_slug) == ALL_REMOVE

    def test_malformed_per_feed_json_ignored(self, db, feed_slug):
        db.update_podcast(feed_slug, segment_category_actions='not-json')
        assert db.resolve_segment_actions(feed_slug) == ALL_REMOVE

    def test_passing_in_podcast_dict_avoids_extra_lookup(self, db, feed_slug):
        db.update_podcast(
            feed_slug, segment_category_actions=json.dumps({'sponsor': 'beep'}))
        podcast = db.get_podcast_by_slug(feed_slug)
        expected = dict(ALL_REMOVE, sponsor='beep')
        assert db.resolve_segment_actions(feed_slug, podcast=podcast) == expected


class TestPutGlobalValidation:
    def test_put_partial_map_merges_over_stored(self, client, db):
        resp = client.put(
            '/api/v1/settings/ad-detection',
            data=json.dumps({'segmentCategoryActions': {'intro': 'keep'}}),
            content_type='application/json',
        )
        assert resp.status_code == 200, resp.data
        assert json.loads(db.get_setting('segment_category_actions'))['intro'] == 'keep'

        resp = client.put(
            '/api/v1/settings/ad-detection',
            data=json.dumps({'segmentCategoryActions': {'outro': 'beep'}}),
            content_type='application/json',
        )
        assert resp.status_code == 200, resp.data
        stored = json.loads(db.get_setting('segment_category_actions'))
        assert stored['intro'] == 'keep'
        assert stored['outro'] == 'beep'

    def test_put_rejects_unknown_category(self, client):
        resp = client.put(
            '/api/v1/settings/ad-detection',
            data=json.dumps({'segmentCategoryActions': {'bogus': 'remove'}}),
            content_type='application/json',
        )
        assert resp.status_code == 400
        assert 'segmentCategoryActions' in json.loads(resp.data)['error']

    def test_put_rejects_unknown_action(self, client):
        resp = client.put(
            '/api/v1/settings/ad-detection',
            data=json.dumps({'segmentCategoryActions': {'intro': 'bogus'}}),
            content_type='application/json',
        )
        assert resp.status_code == 400
        assert 'segmentCategoryActions' in json.loads(resp.data)['error']

    def test_put_rejects_non_object(self, client):
        resp = client.put(
            '/api/v1/settings/ad-detection',
            data=json.dumps({'segmentCategoryActions': 'nope'}),
            content_type='application/json',
        )
        assert resp.status_code == 400


class TestGetSettingsExposure:
    def test_defaults_block_exposes_merged_map(self, client, db):
        conn = db.get_connection()
        conn.execute("DELETE FROM settings WHERE key = 'segment_category_actions'")
        conn.commit()
        data = _get_settings(client)
        assert data['defaults']['segmentCategoryActions'] == ALL_REMOVE

    def test_top_level_reflects_current_global_value(self, client, db):
        db.set_setting(
            'segment_category_actions', json.dumps({'recap': 'keep'}), is_default=False)
        data = _get_settings(client)
        assert data['segmentCategoryActions']['value']['recap'] == 'keep'
        assert data['segmentCategoryActions']['isDefault'] is False


class TestPatchPerFeed:
    def test_patch_partial_map_stored_as_is(self, client, feed_slug, db):
        resp = client.patch(
            f'/api/v1/feeds/{feed_slug}',
            json={'segmentCategoryActions': {'intro': 'keep'}},
        )
        assert resp.status_code == 200, resp.data
        assert resp.get_json()['segmentCategoryActions'] == {'intro': 'keep'}
        assert (db.get_podcast_by_slug(feed_slug)['segment_category_actions']
                == json.dumps({'intro': 'keep'}))

    def test_patch_null_clears(self, client, feed_slug, db):
        client.patch(
            f'/api/v1/feeds/{feed_slug}',
            json={'segmentCategoryActions': {'intro': 'keep'}},
        )
        resp = client.patch(
            f'/api/v1/feeds/{feed_slug}',
            json={'segmentCategoryActions': None},
        )
        assert resp.status_code == 200
        assert resp.get_json()['segmentCategoryActions'] is None
        assert db.get_podcast_by_slug(feed_slug)['segment_category_actions'] is None

    def test_patch_unknown_category_rejected(self, client, feed_slug, db):
        resp = client.patch(
            f'/api/v1/feeds/{feed_slug}',
            json={'segmentCategoryActions': {'bogus': 'remove'}},
        )
        assert resp.status_code == 400
        assert 'segmentCategoryActions' in resp.get_json()['error']
        assert db.get_podcast_by_slug(feed_slug)['segment_category_actions'] is None

    def test_patch_unknown_action_rejected(self, client, feed_slug, db):
        resp = client.patch(
            f'/api/v1/feeds/{feed_slug}',
            json={'segmentCategoryActions': {'sponsor': 'bogus'}},
        )
        assert resp.status_code == 400
        assert 'segmentCategoryActions' in resp.get_json()['error']
        assert db.get_podcast_by_slug(feed_slug)['segment_category_actions'] is None

    def test_patch_detect_show_segments_bool(self, client, feed_slug, db):
        resp = client.patch(
            f'/api/v1/feeds/{feed_slug}',
            json={'detectShowSegments': True},
        )
        assert resp.status_code == 200, resp.data
        assert resp.get_json()['detectShowSegments'] is True
        assert db.get_podcast_by_slug(feed_slug)['detect_show_segments'] == 1

        resp = client.patch(
            f'/api/v1/feeds/{feed_slug}',
            json={'detectShowSegments': None},
        )
        assert resp.status_code == 200
        assert resp.get_json()['detectShowSegments'] is None
        assert db.get_podcast_by_slug(feed_slug)['detect_show_segments'] is None
