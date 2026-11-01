import os
import sys
import tempfile
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='sb-submit-api-test-'))

SLUG = 'yt-sb-manual'
EPISODE = 'a1b2c3d4e5f6'
USER_ID = 'u' * 40
MARKERS = [
    {'start': 10.0, 'end': 40.0, 'category': 'sponsor', 'was_cut': True, 'sponsor_known': True},
    {'start': 100.0, 'end': 130.0, 'category': 'self_promo', 'was_cut': True, 'reviewer_verdict': 'confirmed'},
    {'start': 200.0, 'end': 230.0, 'category': 'sponsor', 'was_cut': True},
]
URL = f'/api/v1/feeds/{SLUG}/episodes/{EPISODE}/sponsorblock'


def _authed(client):
    with client.session_transaction() as sess:
        sess['authenticated'] = True
    client.get('/api/v1/auth/status')


def _csrf(client):
    for cookie in client._cookies.values():
        if cookie.key == 'minuspod_csrf':
            return {'X-CSRF-Token': cookie.value}
    return {}


def _enable(db, on=True):
    db.set_setting('youtube_sb_submit_enabled', 'true' if on else 'false', is_default=False)


@pytest.fixture
def seeded(app_client):
    from api import get_database
    db = get_database()
    _authed(app_client)
    db.create_podcast(SLUG, 'youtube://playlist/PL1', title='YT', feed_type='youtube')
    db.upsert_episode(SLUG, EPISODE, title='Ep', status='processed', original_url='https://example.com/a.mp3')
    db.save_episode_details(SLUG, EPISODE, ad_markers=MARKERS)
    db.update_episode_sponsorblock(SLUG, EPISODE, youtube_video_id='vid12345678', sb_state='done')
    _enable(db)
    try:
        with patch('youtube.settings.sb_user_id', return_value=USER_ID):
            yield app_client, db
    finally:
        _enable(db, False)
        db.delete_podcast(SLUG)


def _candidates(client):
    return client.get(f'{URL}/candidates').get_json()


def test_candidates_not_youtube(app_client):
    from api import get_database
    db = get_database()
    _authed(app_client)
    db.create_podcast('plain-sb', 'https://example.com/feed.xml', title='Plain')
    db.upsert_episode('plain-sb', EPISODE, title='Ep', status='processed', original_url='https://example.com/a.mp3')
    try:
        body = app_client.get(f'/api/v1/feeds/plain-sb/episodes/{EPISODE}/sponsorblock/candidates').get_json()
    finally:
        db.delete_podcast('plain-sb')
    assert body == {'allowed': False, 'reason': 'not_youtube', 'segments': []}


def test_candidates_blocked_reasons(seeded):
    client, db = seeded
    _enable(db, False)
    db.upsert_episode(SLUG, EPISODE, title='Ep', status='processing', original_url='https://example.com/a.mp3')
    assert _candidates(client)['reason'] == 'not_completed'
    db.upsert_episode(SLUG, EPISODE, title='Ep', status='processed', original_url='https://example.com/a.mp3')
    assert _candidates(client)['reason'] == 'disabled'
    _enable(db)
    with patch('youtube.settings.sb_user_id', return_value=''):
        assert _candidates(client)['reason'] == 'no_user_id'
    db.update_episode_sponsorblock(SLUG, EPISODE, sb_segment_uuids='["abc"]')
    assert _candidates(client)['reason'] == 'community_marks'
    db.update_episode_sponsorblock(SLUG, EPISODE, sb_segment_uuids=None, youtube_video_id='')
    assert _candidates(client)['reason'] == 'no_video_id'
    db.update_episode_sponsorblock(SLUG, EPISODE, youtube_video_id='vid12345678', sb_state='watching')
    assert _candidates(client)['reason'] == 'marks_unverified'
    assert client.get(f'/api/v1/feeds/{SLUG}/episodes/ffffffffffff/sponsorblock/candidates').status_code == 404


def test_candidates_segments_with_evidence_and_submissions(seeded):
    client, db = seeded
    db.update_episode_sponsorblock(SLUG, EPISODE, sb_submissions='[{"start": 10, "end": 40, '
                                   '"category": "sponsor", "status": "submitted", "uuid": "x"}]')
    body = _candidates(client)
    assert body['allowed'] is True and body['reason'] is None
    first, second = body['segments']
    assert (first['start'], first['end'], first['category'], first['sbCategory']) == (10.0, 40.0, 'sponsor', 'sponsor')
    assert first['evidence'] == ['known sponsor'] and first['submission']['uuid'] == 'x'
    assert second['sbCategory'] == 'selfpromo' and second['evidence'] == ['reviewer confirmed']
    assert second['submission'] is None
    assert len(body['segments']) == 2


def test_candidates_include_segments_confirmed_by_correction(seeded):
    client, db = seeded
    podcast_id = db.get_podcast_by_slug(SLUG)['id']
    db.create_pattern_correction('confirm', episode_id=EPISODE, podcast_id=podcast_id,
                                 original_bounds={'start': 200.0, 'end': 230.0})
    segments = _candidates(client)['segments']
    assert [s['start'] for s in segments] == [10.0, 100.0, 200.0]
    assert segments[2]['evidence'] == ['user confirmed']


def test_boundary_adjustment_does_not_count_as_confirm(seeded):
    client, db = seeded
    podcast_id = db.get_podcast_by_slug(SLUG)['id']
    db.create_pattern_correction('boundary_adjustment', episode_id=EPISODE, podcast_id=podcast_id,
                                 original_bounds={'start': 200.0, 'end': 230.0},
                                 corrected_bounds={'start': 201.0, 'end': 229.0})
    assert [s['start'] for s in _candidates(client)['segments']] == [10.0, 100.0]


def test_shifted_candidate_is_blocked_after_submission(seeded):
    client, db = seeded
    db.update_episode_sponsorblock(SLUG, EPISODE, sb_submissions='[{"start": 10.2, "end": 40.2, '
                                   '"category": "sponsor", "status": "submitted"}]')
    assert _candidates(client)['segments'][0]['submission']['status'] == 'submitted'
    assert _post(client, [{'start': 10.0, 'end': 40.0}]).status_code == 400


def _post(client, segments):
    return client.post(f'{URL}/submit', json={'segments': segments}, headers=_csrf(client))


def test_submit_validation(seeded):
    client, db = seeded
    for body in ([], [{'start': 'a', 'end': 2}], [{'start': 1}], 'x',
                 [{'start': float(i), 'end': float(i) + 1} for i in range(21)]):
        assert _post(client, body).status_code == 400, body
    assert _post(client, [{'start': 11.0, 'end': 40.0}]).status_code == 400
    assert _post(client, [{'start': 200.0, 'end': 230.0}]).status_code == 400
    _enable(db, False)
    assert _post(client, [{'start': 10.0, 'end': 40.0}]).status_code == 400


def test_submit_records_uuid(seeded):
    client, db = seeded
    with patch('youtube.sponsorblock_submit.submit', return_value=('submitted', '', [{'UUID': 'u2', 'segment': [100.0, 130.0]}, {'UUID': 'u1', 'segment': [10.0, 40.0]}])) as send:
        resp = _post(client, [{'start': 10.02, 'end': 40.0}, {'start': 100.0, 'end': 130.0}])
    assert resp.status_code == 200, resp.get_json()
    results = resp.get_json()['results']
    assert [(r['status'], r['uuid'], r['category']) for r in results] == [
        ('submitted', 'u1', 'sponsor'), ('submitted', 'u2', 'selfpromo')]
    payload = send.call_args.args[0]
    assert payload['videoID'] == 'vid12345678' and len(payload['segments']) == 2
    stored = db.get_episode(SLUG, EPISODE)['sb_submissions']
    assert '"u1"' in stored and USER_ID not in str(resp.get_json())
    again = _post(client, [{'start': 10.0, 'end': 40.0}])
    assert again.status_code == 400
    assert _candidates(client)['segments'][0]['submission']['status'] == 'submitted'


def test_submit_duplicate_and_rejected_are_200(seeded):
    client, _ = seeded
    for label, span in (('duplicate', (10.0, 40.0)), ('rejected', (100.0, 130.0))):
        with patch('youtube.sponsorblock_submit.submit', return_value=(label, 'why', [])):
            resp = _post(client, [{'start': span[0], 'end': span[1]}])
        assert resp.status_code == 200 and resp.get_json()['results'][0]['status'] == label


def test_submit_rate_limited_is_429_and_error_is_502_and_retryable(seeded):
    client, _ = seeded
    with patch('youtube.sponsorblock_submit.submit', return_value=('rate_limited', 'slow', [])):
        resp = _post(client, [{'start': 10.0, 'end': 40.0}])
    assert resp.status_code == 429 and resp.get_json()['results'][0]['status'] == 'rate_limited'
    with patch('youtube.sponsorblock_submit.submit', return_value=('error', 'HTTP 500: x', [])):
        resp = _post(client, [{'start': 10.0, 'end': 40.0}])
    assert resp.status_code == 502 and resp.get_json()['results'][0]['status'] == 'error'
    with patch('youtube.sponsorblock_submit.submit', return_value=('submitted', '', [{'UUID': 'ok', 'segment': [10.0, 40.0]}])):
        assert _post(client, [{'start': 10.0, 'end': 40.0}]).status_code == 200


def test_candidates_and_submit_refused_while_sponsorblock_is_unreachable(seeded):
    client, db = seeded
    with patch('youtube.sponsorblock.is_unreachable', return_value=True):
        assert _candidates(client)['reason'] == 'service_down'
        with patch('youtube.sponsorblock_submit.submit') as send:
            assert _post(client, [{'start': 10.0, 'end': 40.0}]).status_code == 400
        send.assert_not_called()
    assert _candidates(client)['reason'] is None
