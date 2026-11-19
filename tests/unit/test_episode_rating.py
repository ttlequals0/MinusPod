import datetime
import json
import os
import sys
import tempfile
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='episode-rating-test-'))

SLUG = 'rating-feed'
EPISODE = 'a1b2c3d4e5f6'
OTHER = 'b1b2c3d4e5f6'
MARKERS = [
    {'start': 10.0, 'end': 40.0, 'category': 'sponsor', 'was_cut': True},
    {'start': 100.0, 'end': 130.0, 'category': 'cross_promo', 'was_cut': True,
     'validation': {'user_confirmed': True}},
    {'start': 200.0, 'end': 230.0, 'category': 'sponsor', 'was_cut': False, 'held_for_review': True},
    {'start': 300.0, 'end': 330.0, 'category': 'sponsor', 'was_cut': False, 'action_applied': 'keep'},
    {'start': 400.0, 'end': 430.0, 'category': 'intro', 'was_cut': True},
    {'start': 500.0, 'end': 530.0, 'category': 'sponsor', 'was_cut': True},
    {'start': 600.0, 'end': 630.0, 'category': 'sponsor', 'was_cut': True, 'source': 'sponsorblock'},
]
URL = f'/api/v1/feeds/{SLUG}/episodes/{EPISODE}/rating'


def _authed(client):
    with client.session_transaction() as sess:
        sess['authenticated'] = True
    client.get('/api/v1/auth/status')


def _csrf(client):
    for cookie in client._cookies.values():
        if cookie.key == 'minuspod_csrf':
            return {'X-CSRF-Token': cookie.value}
    return {}


@pytest.fixture
def seeded(app_client):
    from api import get_database
    db = get_database()
    _authed(app_client)
    db.create_podcast(SLUG, 'https://example.com/feed.xml', title='Rated')
    for episode in (EPISODE, OTHER):
        db.upsert_episode(SLUG, episode, title=f'Ep {episode}', status='processed',
                          original_url='https://example.com/a.mp3')
    db.save_episode_details(SLUG, EPISODE, ad_markers=MARKERS)
    try:
        yield app_client, db
    finally:
        db.delete_podcast(SLUG)
        conn = db.get_connection()
        conn.execute('DELETE FROM pattern_corrections WHERE episode_id IN (?, ?)', (EPISODE, OTHER))
        conn.commit()


def _put(client, body, url=URL):
    return client.put(url, json=body, headers=_csrf(client))


def test_rating_validation(seeded):
    client, _ = seeded
    for body in ({}, {'rating': 0}, {'rating': 6}, {'rating': '3'}, {'rating': 'UP'}, {'rating': 'sideways'},
                 {'rating': None}, {'rating': True}, {'rating': 2.5},
                 {'rating': 'down', 'notes': 5}, {'rating': 'down', 'notes': 'x' * 2001}):
        assert _put(client, body).status_code == 400, body
    assert 'up' in _put(client, {'rating': 'meh'}).get_json()['error']
    assert _put(client, {'rating': 'down'}, f'/api/v1/feeds/{SLUG}/episodes/ffffffffffff/rating').status_code == 404
    assert client.delete(f'/api/v1/feeds/{SLUG}/episodes/ffffffffffff/rating',
                         headers=_csrf(client)).status_code == 404


def test_notes_are_trimmed_and_cleared_on_up(seeded):
    client, _ = seeded
    body = _put(client, {'rating': 'down', 'notes': '  missed an ad  '}).get_json()
    assert body['rating']['value'] == 'down' and body['rating']['notes'] == 'missed an ad'
    assert body['rating']['ratedAt'] and body['confirmedMarkers'] == 0
    assert _put(client, {'rating': 'down', 'notes': '   '}).get_json()['rating']['notes'] is None
    assert _put(client, {'rating': 'down', 'notes': 'x' * 2000}).status_code == 200
    up = _put(client, {'rating': 'up', 'notes': 'ignored'}).get_json()
    assert up['rating']['value'] == 'up' and up['rating']['notes'] is None


def test_stored_values_and_legacy_integers(seeded):
    client, db = seeded
    stored = lambda: db.get_episode(SLUG, EPISODE)['quality_rating']
    _put(client, {'rating': 'up'})
    assert stored() == 5
    _put(client, {'rating': 'down', 'notes': 'n'})
    assert stored() == 1
    for legacy, label, column in ((5, 'up', 5), (4, 'down', 1), (3, 'down', 1), (1, 'down', 1)):
        body = _put(client, {'rating': legacy, 'notes': 'kept' if label == 'down' else None}).get_json()
        assert body['rating']['value'] == label and stored() == column, legacy
    assert body['rating']['notes'] == 'kept'


def test_legacy_star_rows_read_as_thumbs(seeded):
    client, db = seeded
    conn = db.get_connection()
    for stars, label, notes in ((5, 'up', None), (4, 'down', 'old note'), (1, 'down', None)):
        conn.execute('UPDATE episodes SET quality_rating = ?, quality_notes = ?, quality_rated_at = ? '
                     'WHERE episode_id = ?', (stars, notes, '2026-10-01T00:00:00Z', EPISODE))
        conn.commit()
        rating = client.get(f'/api/v1/feeds/{SLUG}/episodes/{EPISODE}').get_json()['rating']
        assert (rating['value'], rating['notes']) == (label, notes), stars


def test_delete_clears_rating(seeded):
    client, db = seeded
    _put(client, {'rating': 'down', 'notes': 'bad'})
    resp = client.delete(URL, headers=_csrf(client))
    assert resp.status_code == 200 and resp.get_json() == {'rating': None}
    row = db.get_episode(SLUG, EPISODE)
    assert (row['quality_rating'], row['quality_notes'], row['quality_rated_at']) == (None, None, None)


def test_thumbs_up_skips_community_marks_and_confirms_other_cut_ads(seeded):
    client, db = seeded
    podcast_id = db.get_podcast_by_slug(SLUG)['id']
    before = db.get_episode(SLUG, EPISODE)['ad_markers_json']
    resp = _put(client, {'rating': 'up'})
    assert resp.get_json()['confirmedMarkers'] == 2
    assert resp.get_json()['dismissedMarkers'] == 1
    corrections = db.get_episode_corrections(podcast_id, EPISODE)
    assert sorted(c['original_bounds']['start'] for c in corrections) == [10.0, 500.0]
    assert {c['correction_type'] for c in corrections} == {'confirm'}
    row = db.get_episode(SLUG, EPISODE)
    after, prior = json.loads(row['ad_markers_json']), json.loads(before)
    def untouched(markers):
        return [m for m in markers if m['start'] != MARKERS[2]['start']]
    assert untouched(after) == untouched(prior)
    assert row['pending_recut_at'] is None
    assert _put(client, {'rating': 'up'}).get_json()['confirmedMarkers'] == 0
    assert len(db.get_episode_corrections(podcast_id, EPISODE)) == 2


def _seed_cut_and_held(db):
    held = {**MARKERS[2], 'hold_reason': 'max_duration'}
    db.save_episode_details(SLUG, EPISODE, ad_markers=[MARKERS[0], held], pending_review_count=1)


def test_thumbs_up_dismisses_held_markers_without_a_correction(seeded):
    client, db = seeded
    _seed_cut_and_held(db)
    body = _put(client, {'rating': 'up'}).get_json()
    assert (body['confirmedMarkers'], body['dismissedMarkers']) == (1, 1)
    row = db.get_episode(SLUG, EPISODE)
    held = json.loads(row['ad_markers_json'])[1]
    assert held['held_for_review'] is False and held['was_cut'] is False
    assert 'hold_reason' not in held and held['validation']['decision'] == 'REJECT'
    assert not row['pending_review_count']
    podcast_id = db.get_podcast_by_slug(SLUG)['id']
    assert not [c for c in db.get_episode_corrections(podcast_id, EPISODE)
                if c['correction_type'] == 'false_positive']


def test_confirm_markers_must_be_a_boolean(seeded):
    client, _ = seeded
    for value in ('yes', 1, 0, []):
        assert _put(client, {'rating': 'up', 'confirm_markers': value}).status_code == 400, value


def test_confirm_markers_true_confirms_on_up(seeded):
    client, db = seeded
    _seed_cut_and_held(db)
    body = _put(client, {'rating': 'up', 'confirm_markers': True}).get_json()
    assert (body['confirmedMarkers'], body['dismissedMarkers']) == (1, 1)


def test_confirm_markers_false_only_stores_the_rating(seeded):
    client, db = seeded
    _seed_cut_and_held(db)
    body = _put(client, {'rating': 'up', 'confirm_markers': False}).get_json()
    assert (body['confirmedMarkers'], body['dismissedMarkers']) == (0, 0)
    assert body['rating']['value'] == 'up'
    assert json.loads(db.get_episode(SLUG, EPISODE)['ad_markers_json'])[1]['held_for_review'] is True
    assert not db.get_episode_corrections(db.get_podcast_by_slug(SLUG)['id'], EPISODE)


def test_confirm_markers_true_on_down_does_nothing(seeded):
    client, db = seeded
    _seed_cut_and_held(db)
    body = _put(client, {'rating': 'down', 'confirm_markers': True}).get_json()
    assert (body['confirmedMarkers'], body['dismissedMarkers']) == (0, 0)


def test_thumbs_down_leaves_held_markers(seeded):
    client, db = seeded
    _seed_cut_and_held(db)
    assert _put(client, {'rating': 'down'}).get_json()['dismissedMarkers'] == 0
    assert json.loads(db.get_episode(SLUG, EPISODE)['ad_markers_json'])[1]['held_for_review'] is True


def test_thumbs_up_without_held_markers_does_not_rewrite_markers(seeded):
    client, db = seeded
    db.save_episode_details(SLUG, EPISODE, ad_markers=[MARKERS[0]])
    with patch.object(type(db), 'save_episode_details') as save:
        assert _put(client, {'rating': 'up'}).get_json()['dismissedMarkers'] == 0
    save.assert_not_called()


def test_rating_in_episode_and_feed_payloads(seeded):
    client, _ = seeded
    detail = client.get(f'/api/v1/feeds/{SLUG}/episodes/{EPISODE}').get_json()
    assert detail['rating'] is None
    feed = client.get(f'/api/v1/feeds/{SLUG}').get_json()
    assert 'ratingAverage' not in feed and 'ratingCount' not in feed
    _put(client, {'rating': 'down', 'notes': 'ok'})
    _put(client, {'rating': 'down', 'notes': 'bad'}, f'/api/v1/feeds/{SLUG}/episodes/{OTHER}/rating')
    detail = client.get(f'/api/v1/feeds/{SLUG}/episodes/{EPISODE}').get_json()
    assert detail['rating']['value'] == 'down' and detail['rating']['notes'] == 'ok'
    listed = {e['id'] if 'id' in e else e['episodeId']: e for e in
              client.get(f'/api/v1/feeds/{SLUG}/episodes').get_json()['episodes']}
    assert listed[OTHER]['rating']['value'] == 'down'
    feed = client.get(f'/api/v1/feeds/{SLUG}').get_json()
    assert 'ratingAverage' not in feed and 'ratingCount' not in feed


def test_diagnostic_export_includes_ratings_in_range(seeded):
    client, db = seeded
    _put(client, {'rating': 'down', 'notes': 'skipped nothing'})
    now = datetime.datetime.now(datetime.timezone.utc)
    stamp = (now - datetime.timedelta(hours=30)).strftime('%Y-%m-%dT%H:%M:%SZ')
    _put(client, {'rating': 'down', 'notes': 'old'}, f'/api/v1/feeds/{SLUG}/episodes/{OTHER}/rating')
    conn = db.get_connection()
    conn.execute('UPDATE episodes SET quality_rated_at = ? WHERE episode_id = ?', (stamp, OTHER))
    conn.commit()
    body = client.get('/api/v1/system/diagnostic-export').get_json()
    assert body['coverage']['description'] == 'structured operational metadata plus episode ratings and notes'
    ratings = [r for r in body['episodeRatings'] if r['slug'] == SLUG]
    assert len(ratings) == 1
    entry = ratings[0]
    assert (entry['episodeId'], entry['title'], entry['rating'], entry['notes']) == (
        EPISODE, f'Ep {EPISODE}', 'down', 'skipped nothing')
    assert entry['markers'] == {'cut': 5, 'kept': 1, 'held': 1}
    assert entry['ratedAt']
