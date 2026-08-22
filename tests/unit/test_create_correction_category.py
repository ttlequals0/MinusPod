"""Category on manual ad creation (`create` correction, DTNS 5337).

The create correction is the UI's pattern-creation path; the chosen
category must land on both the new pattern (so future matches resolve the
right segment action) and the manual marker itself.
"""
import json
import os
import tempfile

import pytest

os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='create_cat_test_'))
os.environ.setdefault('SECRET_KEY', 'test-secret')

from main_app import app

SLUG = 'create-category-test'
EPISODE_ID = 'abc123def003'

AD_TEXT = (
    'Morning Brew Daily covers the biggest stories in business and tech '
    'every weekday morning. Subscribe wherever you get your podcasts.'
)


@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c


def _seed(temp_db, episode_id):
    temp_db.create_podcast(SLUG, 'https://example.com/feed.xml', 'Create Category Test')
    temp_db.upsert_episode(
        slug=SLUG, episode_id=episode_id,
        original_url='https://example.com/ep.mp3',
        title='Test Episode', original_duration=3600.0,
    )


def _payload(**overrides):
    payload = {
        'type': 'create', 'start': 10.0, 'end': 40.0,
        'sponsor': 'Morning Brew', 'text_template': AD_TEXT,
    }
    payload.update(overrides)
    return payload


def _created_pattern(temp_db, episode_id):
    conn = temp_db.get_connection()
    row = conn.execute(
        'SELECT * FROM ad_patterns WHERE created_from_episode_id = ?',
        (episode_id,),
    ).fetchone()
    return dict(row) if row else None


def _manual_marker(temp_db, episode_id):
    episode = temp_db.get_episode(SLUG, episode_id)
    markers = json.loads(episode['ad_markers_json'])
    return next(m for m in markers if m.get('detection_stage') == 'manual')


def test_create_correction_stamps_category_on_pattern_and_marker(client, temp_db):
    _seed(temp_db, EPISODE_ID)
    r = client.post(
        f'/api/v1/episodes/{SLUG}/{EPISODE_ID}/corrections',
        json=_payload(category='cross_promo'),
    )
    assert r.status_code == 200, r.get_data(as_text=True)
    assert _created_pattern(temp_db, EPISODE_ID)['category'] == 'cross_promo'
    assert _manual_marker(temp_db, EPISODE_ID)['category'] == 'cross_promo'


def test_create_correction_without_category_leaves_both_unset(client, temp_db):
    episode_id = 'abc123def004'
    _seed(temp_db, episode_id)
    r = client.post(
        f'/api/v1/episodes/{SLUG}/{episode_id}/corrections',
        json=_payload(),
    )
    assert r.status_code == 200, r.get_data(as_text=True)
    assert _created_pattern(temp_db, episode_id).get('category') is None
    assert 'category' not in _manual_marker(temp_db, episode_id)


def test_create_correction_rejects_unknown_category(client, temp_db):
    episode_id = 'abc123def005'
    _seed(temp_db, episode_id)
    r = client.post(
        f'/api/v1/episodes/{SLUG}/{episode_id}/corrections',
        json=_payload(category='advertisement'),
    )
    assert r.status_code == 400
    assert _created_pattern(temp_db, episode_id) is None
