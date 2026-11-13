"""A confirmed or adjusted marker's category reaches the pattern it teaches (#565)."""
import json
from unittest.mock import MagicMock, patch

import pytest

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('confirm_category_test_')
from main_app import app

SLUG = 'confirm-category-podcast'
EPISODE_ID = 'abc123def565'

AD_TEXT = (
    "[00:00:00.000 --> 00:00:30.000] Designer Shoe Warehouse has the best "
    "selection of shoes available online. Head to dsw dot com slash podcast "
    "for twenty percent off your first order from Designer Shoe Warehouse today."
)


@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c


def _mock_db(markers):
    db = MagicMock()
    db.get_transcript_for_timestamps.return_value = AD_TEXT
    db.get_podcast_by_slug.return_value = {'id': 99, 'slug': SLUG}
    db.get_podcast_row.return_value = {'id': 99, 'slug': SLUG}
    db.get_episode.return_value = {'ad_markers_json': json.dumps(markers)}
    db.get_setting_float.side_effect = lambda key, default=None: default
    db.get_setting_bool.side_effect = lambda key, default=None: default
    return db


def _post(client, db, payload):
    with patch('api.patterns.get_database', return_value=db):
        return client.post(
            f'/api/v1/episodes/{SLUG}/{EPISODE_ID}/corrections',
            data=json.dumps(payload), content_type='application/json',
        )


@pytest.mark.parametrize('category, expected', [
    ('interaction', 'interaction'),
    ('self_promo', 'self_promo'),
    ('sponsor', 'sponsor'),
    (None, None),
    ('not_a_category', None),
])
def test_confirm_passes_marker_category(client, category, expected):
    marker = {'start': 0.0, 'end': 30.0, 'confidence': 0.9}
    if category is not None:
        marker['category'] = category
    db = _mock_db([marker])
    with patch('api.patterns._resolve_or_create_pattern_from_text') as mock_resolve:
        mock_resolve.return_value = (None, [])
        resp = _post(client, db, {'type': 'confirm', 'original_ad': {'start': 0.0, 'end': 30.0}})
    assert resp.status_code == 200
    mock_resolve.assert_called_once()
    assert mock_resolve.call_args.kwargs.get('category') == expected


def test_confirm_without_a_matching_marker_passes_none(client):
    db = _mock_db([{'start': 500.0, 'end': 560.0, 'category': 'interaction'}])
    with patch('api.patterns._resolve_or_create_pattern_from_text') as mock_resolve:
        mock_resolve.return_value = (None, [])
        resp = _post(client, db, {'type': 'confirm', 'original_ad': {'start': 0.0, 'end': 30.0}})
    assert resp.status_code == 200
    assert mock_resolve.call_args.kwargs.get('category') is None


def test_adjust_passes_marker_category(client):
    db = _mock_db([{'start': 0.0, 'end': 35.0, 'category': 'interaction'}])
    with patch('api.patterns._resolve_or_create_pattern_from_text') as mock_resolve:
        mock_resolve.return_value = (None, [])
        resp = _post(client, db, {
            'type': 'adjust', 'original_ad': {'start': 0.0, 'end': 35.0},
            'adjusted_start': 0.0, 'adjusted_end': 30.0,
        })
    assert resp.status_code == 200
    assert mock_resolve.call_args.kwargs.get('category') == 'interaction'


def test_confirmed_pattern_stores_the_category(temp_db):
    """End to end through pattern creation: the stored pattern reads back the category."""
    from api.patterns import _resolve_or_create_pattern_from_text
    from pattern_service import PatternService

    slug = 'confirm-category-store'
    episode_id = 'deadbeef0565'
    temp_db.create_podcast(slug, 'https://example.com/feed.xml', 'Confirm Category Store')
    temp_db.upsert_episode(
        slug=slug, episode_id=episode_id, original_url='https://example.com/ep.mp3',
        title='Test Episode', original_duration=3600.0,
    )
    ad_text = (
        "Designer Shoe Warehouse has the best selection of shoes available "
        "online. Head to dsw dot com slash podcast for twenty percent off your "
        "first order from Designer Shoe Warehouse today and every day this week."
    )
    primary_id, _ = _resolve_or_create_pattern_from_text(
        temp_db, PatternService(temp_db), slug, episode_id, ad_text,
        {'start': 0.0, 'end': 30.0}, label='confirmed',
        sponsor_override='Designer Shoe Warehouse', category='self_promo',
    )
    assert primary_id is not None
    assert temp_db.get_ad_pattern_by_id(primary_id)['category'] == 'self_promo'
