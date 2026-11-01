import datetime
import os
import sys
import tempfile

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
    for body in ({}, {'rating': 0}, {'rating': 6}, {'rating': '3'}, {'rating': True}, {'rating': 2.5},
                 {'rating': 3, 'notes': 5}, {'rating': 3, 'notes': 'x' * 2001}):
        assert _put(client, body).status_code == 400, body
    assert _put(client, {'rating': 3}, f'/api/v1/feeds/{SLUG}/episodes/ffffffffffff/rating').status_code == 404
    assert client.delete(f'/api/v1/feeds/{SLUG}/episodes/ffffffffffff/rating',
                         headers=_csrf(client)).status_code == 404


def test_notes_are_trimmed_and_cleared_on_five(seeded):
    client, _ = seeded
    body = _put(client, {'rating': 3, 'notes': '  missed an ad  '}).get_json()
    assert body['rating']['value'] == 3 and body['rating']['notes'] == 'missed an ad'
    assert body['rating']['ratedAt'] and body['confirmedMarkers'] == 0
    assert _put(client, {'rating': 2, 'notes': '   '}).get_json()['rating']['notes'] is None
    assert _put(client, {'rating': 2, 'notes': 'x' * 2000}).status_code == 200
    five = _put(client, {'rating': 5, 'notes': 'ignored'}).get_json()
    assert five['rating']['value'] == 5 and five['rating']['notes'] is None


def test_delete_clears_rating(seeded):
    client, db = seeded
    _put(client, {'rating': 1, 'notes': 'bad'})
    resp = client.delete(URL, headers=_csrf(client))
    assert resp.status_code == 200 and resp.get_json() == {'rating': None}
    row = db.get_episode(SLUG, EPISODE)
    assert (row['quality_rating'], row['quality_notes'], row['quality_rated_at']) == (None, None, None)


def test_five_stars_skips_community_marks_and_confirms_other_cut_ads(seeded):
    client, db = seeded
    podcast_id = db.get_podcast_by_slug(SLUG)['id']
    before = db.get_episode(SLUG, EPISODE)['ad_markers_json']
    resp = _put(client, {'rating': 5})
    assert resp.get_json()['confirmedMarkers'] == 2
    corrections = db.get_episode_corrections(podcast_id, EPISODE)
    assert sorted(c['original_bounds']['start'] for c in corrections) == [10.0, 500.0]
    assert {c['correction_type'] for c in corrections} == {'confirm'}
    row = db.get_episode(SLUG, EPISODE)
    assert row['ad_markers_json'] == before
    assert row['pending_recut_at'] is None
    assert _put(client, {'rating': 5}).get_json()['confirmedMarkers'] == 0
    assert len(db.get_episode_corrections(podcast_id, EPISODE)) == 2


def test_rating_in_episode_and_feed_payloads(seeded):
    client, _ = seeded
    detail = client.get(f'/api/v1/feeds/{SLUG}/episodes/{EPISODE}').get_json()
    assert detail['rating'] is None
    feed = client.get(f'/api/v1/feeds/{SLUG}').get_json()
    assert feed['ratingAverage'] is None and feed['ratingCount'] == 0
    _put(client, {'rating': 4, 'notes': 'ok'})
    _put(client, {'rating': 1, 'notes': 'bad'}, f'/api/v1/feeds/{SLUG}/episodes/{OTHER}/rating')
    detail = client.get(f'/api/v1/feeds/{SLUG}/episodes/{EPISODE}').get_json()
    assert detail['rating']['value'] == 4 and detail['rating']['notes'] == 'ok'
    listed = {e['id'] if 'id' in e else e['episodeId']: e for e in
              client.get(f'/api/v1/feeds/{SLUG}/episodes').get_json()['episodes']}
    assert listed[OTHER]['rating']['value'] == 1
    feed = client.get(f'/api/v1/feeds/{SLUG}').get_json()
    assert feed['ratingAverage'] == 2.5 and feed['ratingCount'] == 2


def test_diagnostic_export_includes_ratings_in_range(seeded):
    client, db = seeded
    _put(client, {'rating': 2, 'notes': 'skipped nothing'})
    now = datetime.datetime.now(datetime.timezone.utc)
    stamp = (now - datetime.timedelta(hours=30)).strftime('%Y-%m-%dT%H:%M:%SZ')
    _put(client, {'rating': 3, 'notes': 'old'}, f'/api/v1/feeds/{SLUG}/episodes/{OTHER}/rating')
    conn = db.get_connection()
    conn.execute('UPDATE episodes SET quality_rated_at = ? WHERE episode_id = ?', (stamp, OTHER))
    conn.commit()
    body = client.get('/api/v1/system/diagnostic-export').get_json()
    assert body['coverage']['description'] == 'structured operational metadata plus episode ratings and notes'
    ratings = [r for r in body['episodeRatings'] if r['slug'] == SLUG]
    assert len(ratings) == 1
    entry = ratings[0]
    assert (entry['episodeId'], entry['title'], entry['rating'], entry['notes']) == (
        EPISODE, f'Ep {EPISODE}', 2, 'skipped nothing')
    assert entry['markers'] == {'cut': 5, 'kept': 1, 'held': 1}
    assert entry['ratedAt']
