"""API tests for the community_sync_categories global setting.

Covers the SETTINGS_REGISTRY / SettingSpec + PUT-phase pattern (mirroring
segment_category_actions), including the top-level _sv entry in GET
/settings, plus the dedicated /settings/community-sync GET/PUT surface the
CommunityPatternsSection UI talks to. Each test patches
api.settings.get_database to a fresh temp_db to avoid order-dependence
between tests sharing the same setting key.
"""
import json
from unittest.mock import patch

import pytest

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('community_sync_cat_api_test_')
from config import SEGMENT_CATEGORIES  # noqa: E402
from main_app import app  # noqa: E402


@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c


class TestGetSettingsTopLevelEntry:
    """The regression this guards: a new registry key present only in the
    'defaults' block (derived generically from SETTINGS_REGISTRY) but with
    no corresponding top-level _sv(...) entry in the GET /settings payload."""

    def test_top_level_key_present_alongside_defaults_block(self, client, temp_db):
        with patch('api.settings.get_database', return_value=temp_db):
            s = client.get('/api/v1/settings').get_json()
        assert 'communitySyncCategories' in s
        assert s['communitySyncCategories']['value'] == list(SEGMENT_CATEGORIES)
        assert s['communitySyncCategories']['isDefault'] is True
        assert s['defaults']['communitySyncCategories'] == list(SEGMENT_CATEGORIES)


class TestAdDetectionPutPhase:
    def _put(self, client, payload):
        return client.put(
            '/api/v1/settings/ad-detection',
            data=json.dumps(payload),
            content_type='application/json',
        )

    def test_put_roundtrip(self, client, temp_db):
        with patch('api.settings.get_database', return_value=temp_db):
            r = self._put(client, {'communitySyncCategories': ['sponsor', 'cross_promo']})
            assert r.status_code == 200, r.data
            s = client.get('/api/v1/settings').get_json()
        assert s['communitySyncCategories']['value'] == ['sponsor', 'cross_promo']
        assert s['communitySyncCategories']['isDefault'] is False

    def test_rejects_unknown_category(self, client, temp_db):
        with patch('api.settings.get_database', return_value=temp_db):
            r = self._put(client, {'communitySyncCategories': ['not-a-category']})
        assert r.status_code == 400
        assert 'not-a-category' in r.get_json()['error']

    def test_rejects_non_list(self, client, temp_db):
        with patch('api.settings.get_database', return_value=temp_db):
            r = self._put(client, {'communitySyncCategories': 'sponsor'})
        assert r.status_code == 400

    def test_empty_list_is_a_valid_accept_nothing_choice(self, client, temp_db):
        with patch('api.settings.get_database', return_value=temp_db):
            r = self._put(client, {'communitySyncCategories': []})
            assert r.status_code == 200, r.data
            s = client.get('/api/v1/settings').get_json()
        assert s['communitySyncCategories']['value'] == []


class TestCommunitySyncEndpoint:
    def test_get_includes_categories_and_breakdown(self, client, temp_db):
        with patch('api.settings.get_database', return_value=temp_db):
            r = client.get('/api/v1/settings/community-sync')
        assert r.status_code == 200, r.data
        body = r.get_json()
        assert body['categories'] == list(SEGMENT_CATEGORIES)
        assert body['categoryBreakdown'] == {cat: 0 for cat in SEGMENT_CATEGORIES}

    def test_put_categories_validates_and_persists(self, client, temp_db):
        with patch('api.settings.get_database', return_value=temp_db):
            r = client.put(
                '/api/v1/settings/community-sync',
                data=json.dumps({'categories': ['sponsor', 'interaction']}),
                content_type='application/json',
            )
            assert r.status_code == 200, r.data
            r2 = client.get('/api/v1/settings/community-sync')
        assert r.get_json()['categories'] == ['sponsor', 'interaction']
        assert r2.get_json()['categories'] == ['sponsor', 'interaction']

    def test_put_rejects_unknown_category(self, client, temp_db):
        with patch('api.settings.get_database', return_value=temp_db):
            r = client.put(
                '/api/v1/settings/community-sync',
                data=json.dumps({'categories': ['bogus']}),
                content_type='application/json',
            )
        assert r.status_code == 400

    def test_category_breakdown_counts_active_community_patterns_only(self, client, temp_db):
        temp_db.create_ad_pattern(
            scope='global', text_template='x' * 60,
            source='community', community_id='cid-a', category='sponsor',
        )
        temp_db.create_ad_pattern(
            scope='global', text_template='y' * 60,
            source='community', community_id='cid-b', category='cross_promo',
        )
        inactive_id = temp_db.create_ad_pattern(
            scope='global', text_template='z' * 60,
            source='community', community_id='cid-c', category='cross_promo',
        )
        temp_db.update_ad_pattern(inactive_id, is_active=0)

        with patch('api.settings.get_database', return_value=temp_db):
            body = client.get('/api/v1/settings/community-sync').get_json()
        assert body['categoryBreakdown']['sponsor'] == 1
        # Only the active cross_promo row counts; the deactivated one does not.
        assert body['categoryBreakdown']['cross_promo'] == 1
