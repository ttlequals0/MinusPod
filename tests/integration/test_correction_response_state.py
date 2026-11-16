"""The corrections response reports the marker's state after the write."""
import pytest

from api import get_database

SLUG = 'resp-state-feed'
EPISODE = 'c1d2e3f4a5b6'
DURATION = 3600.0

HELD = {
    'start': 120.0, 'end': 437.0, 'confidence': 0.9, 'sponsor': 'Acme',
    'reason': 'held span', 'category': 'sponsor', 'action_applied': 'remove',
    'detection_stage': 'first_pass', 'was_cut': False, 'held_for_review': True,
    'hold_reason': 'reviewer_boundary_conflict',
}
CUT = {
    'start': 900.0, 'end': 960.0, 'confidence': 0.95, 'sponsor': 'Beta',
    'reason': 'cut span', 'category': 'sponsor', 'action_applied': 'remove',
    'detection_stage': 'first_pass', 'was_cut': True,
}


pytestmark = pytest.mark.usefixtures('held_widen_sixty')


def _csrf(client):
    with client.session_transaction() as sess:
        sess['authenticated'] = True
    client.get('/api/v1/auth/status')
    cookie = client.get_cookie('minuspod_csrf')
    return {'X-CSRF-Token': cookie.value} if cookie else {}


@pytest.fixture
def ep(app_client):
    db = get_database()
    db.create_podcast(SLUG, 'https://example.com/feed.xml', 'Resp Show')
    db.upsert_episode(slug=SLUG, episode_id=EPISODE,
                      original_url='https://example.com/ep.mp3', title='Ep',
                      original_duration=DURATION, status='processed')
    db.save_episode_details(SLUG, EPISODE, transcript_text='[00:00:00.000 --> 00:00:30.000] hi',
                            ad_markers=[dict(HELD), dict(CUT)])
    conn = db.get_connection()
    conn.execute('DELETE FROM pattern_corrections WHERE episode_id = ?', (EPISODE,))
    conn.commit()
    yield app_client
    db.delete_podcast(SLUG)


def _post(client, body):
    return client.post(f'/api/v1/episodes/{SLUG}/{EPISODE}/corrections',
                       json=body, headers=_csrf(client))


def _held(start=120.0, end=437.0):
    return {'start': start, 'end': end}


def test_confirm_trim_response_reports_new_bounds_and_uncut(ep):
    resp = _post(ep, {'type': 'confirm', 'original_ad': _held(),
                      'adjusted_start': 150.0, 'adjusted_end': 400.0})
    body = resp.get_json()
    assert resp.status_code == 200
    assert body['recut_pending'] is True
    assert body['marker'] == {'start': 150.0, 'end': 400.0, 'approved': True,
                              'was_cut': False,
                              'hold_reason': 'reviewer_boundary_conflict'}


def test_confirm_untrimmed_response_reports_approved(ep):
    body = _post(ep, {'type': 'confirm', 'original_ad': _held()}).get_json()
    assert body['marker']['approved'] is True
    assert (body['marker']['start'], body['marker']['end']) == (120.0, 437.0)
    assert body['marker']['was_cut'] is False


def test_adjust_on_held_response_reports_moved_marker(ep):
    body = _post(ep, {'type': 'adjust', 'original_ad': _held(),
                      'adjusted_start': 200.0, 'adjusted_end': 300.0}).get_json()
    assert body['recut_pending'] is True
    assert (body['marker']['start'], body['marker']['end']) == (200.0, 300.0)
    assert body['marker']['approved'] is True


def test_reject_response_reports_marker_state(ep):
    body = _post(ep, {'type': 'reject', 'original_ad': _held()}).get_json()
    assert body['recut_pending'] is True
    assert body['marker']['approved'] is False
    assert body['marker']['was_cut'] is False
    assert body['marker']['hold_reason'] is None


def test_recategorize_response_reports_marker_state(ep):
    body = _post(ep, {'type': 'recategorize', 'category': 'sponsor',
                      'original_ad': {'start': 900.0, 'end': 960.0}}).get_json()
    assert body['category'] == 'sponsor'
    assert body['marker']['was_cut'] is True
    assert body['marker']['hold_reason'] is None


def test_response_without_matching_marker_has_no_marker_key(ep):
    resp = _post(ep, {'type': 'confirm', 'original_ad': {'start': 5000.0, 'end': 5030.0}})
    body = resp.get_json()
    assert resp.status_code == 200
    assert 'marker' not in body and 'recut_pending' not in body
    assert body['message'] == 'Correction recorded'


def test_split_and_create_responses_unchanged(ep):
    split = _post(ep, {'type': 'split', 'original_ad': {'start': 900.0, 'end': 960.0},
                       'split_points': [930.0]})
    assert split.status_code == 200
    assert 'marker' not in split.get_json()
    create = _post(ep, {'type': 'create', 'start': 2000.0, 'end': 2060.0,
                        'sponsor': 'Gamma', 'text_template': 'brought to you by Gamma'})
    assert 'marker' not in create.get_json()
    assert 'recut_pending' not in create.get_json()
