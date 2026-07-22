"""End-to-end coverage of the auto-split guard (issue #563) through the real
/corrections endpoint: confirm and adjust, with no pattern_id, on transcript
text spanning three ad-transition phrases. Each should split into three
patterns, with the correction row linked to the sponsor-matched primary.
"""
import json
import os
import tempfile
from unittest.mock import patch

import pytest

os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='auto_split_test_'))
os.environ.setdefault('SECRET_KEY', 'test-secret')

from main_app import app

SLUG = 'auto-split-test'
EPISODE_ID = 'abc123def002'

THREE_SPONSOR_TEXT = (
    "This episode is brought to you by Acme. Acme provides the best "
    "widgets around, visit acme dot com for twenty percent off today. "
    "This episode is sponsored by Widgetco. Widgetco has amazing "
    "deals this week, check out widgetco dot com right now for savings. "
    "Thanks to Spanso for supporting the show, go check out spanso dot "
    "com slash podcast for a free trial of their new gadget service."
)

TRANSCRIPT = f"[00:00:00.000 --> 00:01:30.000] {THREE_SPONSOR_TEXT}"


@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c


def _seed(temp_db):
    temp_db.create_podcast(SLUG, 'https://example.com/feed.xml', 'Auto Split Test')
    temp_db.upsert_episode(
        slug=SLUG, episode_id=EPISODE_ID,
        original_url='https://example.com/ep.mp3',
        title='Test Episode', original_duration=3600.0,
    )
    temp_db.save_episode_details(SLUG, EPISODE_ID, transcript_text=TRANSCRIPT)


def _patterns_for_episode(temp_db):
    conn = temp_db.get_connection()
    rows = conn.execute(
        "SELECT ap.id, ks.name as sponsor FROM ad_patterns ap "
        "LEFT JOIN known_sponsors ks ON ap.sponsor_id = ks.id "
        "WHERE ap.created_from_episode_id = ?",
        (EPISODE_ID,),
    ).fetchall()
    return {r['sponsor']: r['id'] for r in rows}


def test_confirm_with_no_pattern_id_splits_into_three(client, temp_db):
    _seed(temp_db)
    with patch('api.patterns.get_database', return_value=temp_db):
        resp = client.post(
            f'/api/v1/episodes/{SLUG}/{EPISODE_ID}/corrections',
            data=json.dumps({
                'type': 'confirm',
                'original_ad': {
                    'start': 0.0, 'end': 90.0, 'sponsor': 'Widgetco',
                },
            }),
            content_type='application/json',
        )
    assert resp.status_code == 200, resp.data
    body = resp.get_json()

    by_sponsor = _patterns_for_episode(temp_db)
    assert set(by_sponsor) == {'Acme', 'Widgetco', 'Spanso'}
    assert body['pattern_id'] == by_sponsor['Widgetco']

    row = temp_db.get_connection().execute(
        "SELECT pattern_id FROM pattern_corrections WHERE episode_id = ?",
        (EPISODE_ID,),
    ).fetchone()
    assert row['pattern_id'] == by_sponsor['Widgetco']


def test_adjust_with_no_pattern_id_splits_into_three(client, temp_db):
    _seed(temp_db)
    with patch('api.patterns.get_database', return_value=temp_db):
        resp = client.post(
            f'/api/v1/episodes/{SLUG}/{EPISODE_ID}/corrections',
            data=json.dumps({
                'type': 'adjust',
                'original_ad': {'start': 0.0, 'end': 95.0, 'sponsor': 'Acme'},
                'adjusted_start': 0.0, 'adjusted_end': 90.0,
            }),
            content_type='application/json',
        )
    assert resp.status_code == 200, resp.data
    body = resp.get_json()

    by_sponsor = _patterns_for_episode(temp_db)
    assert set(by_sponsor) == {'Acme', 'Widgetco', 'Spanso'}
    assert body['pattern_id'] == by_sponsor['Acme']
