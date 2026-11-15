"""GET episode serves short_ask as the backend decision (is_short_ask), not the stored flag."""
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='short-ask-flag-test-'))

from api import get_database

SLUG = 'short-ask-flag-feed'
EPISODE = 'abc123def456'


def _auth(app_client):
    with app_client.session_transaction() as sess:
        sess['authenticated'] = True


@pytest.fixture
def seeded(app_client):
    db = get_database()
    db.create_podcast(SLUG, 'https://example.com/sa.xml', title='Short Ask Feed')
    markers = [
        {'start': 10.0, 'end': 20.0, 'category': 'sponsor', 'short_ask': True,
         'short_ask_origin': [10.0, 20.0], 'was_cut': True},
        {'start': 100.0, 'end': 144.9, 'category': 'sponsor', 'short_ask': True,
         'short_ask_origin': [100.0, 112.0], 'was_cut': True},
        {'start': 200.0, 'end': 230.0, 'category': 'sponsor', 'was_cut': True},
        {'start': 300.0, 'end': 320.0, 'category': 'sponsor', 'short_ask': True,
         'short_ask_origin': [300.0, 318.0], 'was_cut': True},
    ]
    db.upsert_episode(SLUG, EPISODE, original_url='https://example.com/sa.mp3',
                      title='Episode', status='processed', original_duration=3600.0)
    db.save_episode_details(SLUG, EPISODE, ad_markers=markers)
    yield
    db.delete_podcast(SLUG)


def test_short_ask_follows_backend_decision(app_client, seeded):
    _auth(app_client)
    resp = app_client.get(f'/api/v1/feeds/{SLUG}/episodes/{EPISODE}')
    assert resp.status_code == 200
    body = resp.get_json()
    markers = {m['start']: m for m in body['adMarkers'] + body['rejectedAdMarkers']
               + body['keptMarkers'] + body['pendingReviewMarkers']}
    assert markers[10.0]['short_ask'] is True
    assert markers[100.0]['short_ask'] is False
    assert not markers[200.0].get('short_ask')


def test_short_ask_false_when_only_edge_cap_fails(app_client, seeded):
    _auth(app_client)
    body = app_client.get(f'/api/v1/feeds/{SLUG}/episodes/{EPISODE}').get_json()
    markers = {m['start']: m for m in body['adMarkers'] + body['rejectedAdMarkers']
               + body['keptMarkers'] + body['pendingReviewMarkers']}
    assert markers[300.0]['short_ask'] is False
