"""adjust and confirm-trim on held markers: approval, server-side validation, recut."""
import json

import pytest

from api import get_database
from main_app import processing
from main_app.pipeline import recut
from tests.unit.pass2_test_utils import _user_corrections

SLUG = 'held-adjust-feed'
EPISODE = 'b1c2d3e4f5a6'
DURATION = 3600.0
TRANSCRIPT = '[00:00:00.000 --> 00:00:30.000] hello'

HELD = {
    'start': 120.0, 'end': 437.0, 'confidence': 0.9, 'sponsor': 'Acme',
    'reason': 'held span', 'category': 'sponsor', 'action_applied': 'remove',
    'detection_stage': 'first_pass', 'was_cut': False, 'held_for_review': True,
    'hold_reason': 'transcript_loop',
}


pytestmark = pytest.mark.usefixtures('held_widen_sixty')


def _csrf(app_client):
    with app_client.session_transaction() as sess:
        sess['authenticated'] = True
    app_client.get('/api/v1/auth/status')
    cookie = app_client.get_cookie('minuspod_csrf')
    return {'X-CSRF-Token': cookie.value} if cookie else {}


def _seed(markers):
    db = get_database()
    db.create_podcast(SLUG, 'https://example.com/feed.xml', 'Held Adjust Show')
    db.upsert_episode(slug=SLUG, episode_id=EPISODE,
                      original_url='https://example.com/ep.mp3', title='Ep',
                      original_duration=DURATION, status='processed')
    db.save_episode_details(SLUG, EPISODE, transcript_text=TRANSCRIPT,
                            ad_markers=markers)
    conn = db.get_connection()
    conn.execute('DELETE FROM pattern_corrections WHERE episode_id = ?', (EPISODE,))
    conn.commit()
    return db


@pytest.fixture
def held(app_client):
    db = _seed([dict(HELD)])
    yield {'db': db, 'client': app_client}
    db.delete_podcast(SLUG)


def _post(ref, body):
    client = ref['client']
    return client.post(f'/api/v1/episodes/{SLUG}/{EPISODE}/corrections',
                       json=body, headers=_csrf(client))


def _orig(start=120.0, end=437.0):
    return {'start': start, 'end': end}


def _adjust(ref, a, b, **orig):
    return _post(ref, {'type': 'adjust', 'original_ad': _orig(**orig),
                       'adjusted_start': a, 'adjusted_end': b})


def _confirm_trim(ref, a, b, **orig):
    return _post(ref, {'type': 'confirm', 'original_ad': _orig(**orig),
                       'adjusted_start': a, 'adjusted_end': b})


def _markers(ref):
    ep = ref['db'].get_episode(SLUG, EPISODE)
    return json.loads(ep['ad_markers_json'])


def _pattern_count(ref):
    return ref['db'].get_connection().execute(
        'SELECT COUNT(*) FROM ad_patterns').fetchone()[0]


def _correction_count(ref):
    return ref['db'].get_connection().execute(
        'SELECT COUNT(*) FROM pattern_corrections WHERE episode_id = ?',
        (EPISODE,)).fetchone()[0]


def test_adjust_on_held_marker_moves_and_approves(held):
    resp = _adjust(held, 200.0, 400.0)
    assert resp.status_code == 200
    [m] = _markers(held)
    assert (m['start'], m['end']) == (200.0, 400.0)
    assert m['approved'] is True
    assert (m['reviewer_original_start'], m['reviewer_original_end']) == (120.0, 437.0)


def test_adjust_on_held_marker_keeps_hold_and_uncut(held):
    _adjust(held, 200.0, 400.0)
    [m] = _markers(held)
    assert m['held_for_review'] is True
    assert m['was_cut'] is False


def test_adjust_on_held_marker_wider_than_span_is_accepted(held):
    resp = _adjust(held, 100.0, 490.0)
    assert resp.status_code == 200
    [m] = _markers(held)
    assert (m['start'], m['end']) == (100.0, 490.0)
    assert m['approved'] is True


def test_adjust_from_ad_review_tab_payload_within_window_is_accepted(held):
    # Patterns > Ad review sends no bounds window; the server computes it.
    resp = _post(held, {'type': 'adjust', 'original_ad': _orig(),
                        'adjusted_start': 90.0, 'adjusted_end': 480.0,
                        'sponsor': 'Acme'})
    assert resp.status_code == 200
    [m] = _markers(held)
    assert (m['start'], m['end']) == (90.0, 480.0)


def test_adjust_on_held_marker_beyond_window_returns_400(held):
    before = _pattern_count(held)
    resp = _adjust(held, 40.0, 437.0)
    assert resp.status_code == 400
    assert 'must lie within the reviewed span' in resp.get_json()['error']
    [m] = _markers(held)
    assert (m['start'], m['end']) == (120.0, 437.0)
    assert _pattern_count(held) == before
    assert _adjust(held, 120.0, 520.0).status_code == 400


def test_adjust_outside_episode_returns_400_and_creates_no_pattern(held):
    before_p, before_c = _pattern_count(held), _correction_count(held)
    for a, b in ((-5.0, 300.0), (100.0, DURATION + 10)):
        assert _adjust(held, a, b).status_code == 400
    assert _pattern_count(held) == before_p
    assert _correction_count(held) == before_c
    [m] = _markers(held)
    assert (m['start'], m['end']) == (120.0, 437.0)
    assert not m.get('approved')


def test_second_trim_keeps_first_original_bounds(held):
    assert _confirm_trim(held, 150.0, 400.0).status_code == 200
    assert _confirm_trim(held, 180.0, 380.0, start=150.0, end=400.0).status_code == 200
    [m] = _markers(held)
    assert (m['start'], m['end']) == (180.0, 380.0)
    assert (m['reviewer_original_start'], m['reviewer_original_end']) == (120.0, 437.0)


def test_recut_cuts_held_marker_at_adjusted_bounds(held, monkeypatch):
    assert _adjust(held, 200.0, 400.0).status_code == 200
    monkeypatch.setattr(processing, 'db', held['db'])
    ads_to_remove, *_ = recut._build_recut_ad_list(
        SLUG, EPISODE, [], DURATION, '', 0.80,
        corrections=_user_corrections(SLUG, EPISODE))
    assert [(a['start'], a['end']) for a in ads_to_remove] == [(200.0, 400.0)]


@pytest.mark.parametrize('a,b', [('x', 400.0), (200.0, 'y')])
def test_adjust_non_numeric_returns_400(held, a, b):
    assert _adjust(held, a, b).status_code == 400
    assert _correction_count(held) == 0


def test_adjust_end_before_start_returns_400(held):
    assert _adjust(held, 400.0, 200.0).status_code == 400
    assert _correction_count(held) == 0


def test_adjust_under_one_second_returns_400(held):
    assert _adjust(held, 200.0, 200.2).status_code == 400
    assert _correction_count(held) == 0
    [m] = _markers(held)
    assert not m.get('approved')


def test_confirm_trim_under_one_second_returns_400(held):
    assert _confirm_trim(held, 200.0, 200.2).status_code == 400
    [m] = _markers(held)
    assert (m['start'], m['end']) == (120.0, 437.0)


def test_adjust_on_cut_marker_still_records_without_touching_it(app_client):
    cut = dict(HELD, was_cut=True, held_for_review=False)
    cut.pop('hold_reason')
    db = _seed([cut])
    try:
        ref = {'db': db, 'client': app_client}
        assert _adjust(ref, 200.0, 400.0).status_code == 200
        [m] = _markers(ref)
        assert (m['start'], m['end']) == (120.0, 437.0)
        assert not m.get('approved')
        assert _correction_count(ref) == 1
    finally:
        db.delete_podcast(SLUG)


def test_adjust_beyond_episode_duration_returns_400(held):
    assert _adjust(held, 3500.0, DURATION + 1).status_code == 400


def test_confirm_trim_valid_unchanged(held):
    resp = _confirm_trim(held, 121.0, 436.0)
    assert resp.status_code == 200
    [m] = _markers(held)
    assert (m['start'], m['end']) == (121.0, 436.0)
    assert m['approved'] is True


def test_reject_on_near_duplicate_held_pair_resolves_both_and_deletes_the_confirm(app_client):
    a = dict(HELD, start=909.0, end=1229.0)
    b = dict(HELD, start=909.02, end=1229.02)
    db = _seed([a, b])
    try:
        ref = {'db': db, 'client': app_client}
        confirm = _post(ref, {'type': 'confirm',
                              'original_ad': {'start': 909.0, 'end': 1229.0}})
        assert confirm.status_code == 200
        reject = _post(ref, {'type': 'reject',
                             'original_ad': {'start': 909.02, 'end': 1229.02}})
        assert reject.status_code == 200
        markers = _markers(ref)
        assert all(not m.get('held_for_review') for m in markers)
        confirmed = db.get_confirmed_corrections(
            db.get_podcast_row(SLUG)['id'], EPISODE)
        assert confirmed == []
    finally:
        db.delete_podcast(SLUG)


def test_reject_dropping_a_confirm_unapproves_the_other_held_marker(app_client, monkeypatch):
    undecided = dict(HELD, start=840.0, end=1230.0)
    approved = dict(HELD, start=909.0, end=1229.0)
    db = _seed([undecided, approved])
    try:
        ref = {'db': db, 'client': app_client}
        assert _post(ref, {'type': 'confirm',
                           'original_ad': {'start': 909.0, 'end': 1229.0}}).status_code == 200
        assert [m.get('approved') for m in _markers(ref)] == [None, True]
        assert _post(ref, {'type': 'reject',
                           'original_ad': {'start': 840.0, 'end': 1230.0}}).status_code == 200
        markers = _markers(ref)
        other = next(m for m in markers if m['start'] == 909.0)
        assert not other.get('approved')
        assert other['held_for_review'] is True
        monkeypatch.setattr(processing, 'db', db)
        ads_to_remove, *_ = recut._build_recut_ad_list(
            SLUG, EPISODE, [], DURATION, '', 0.80,
            corrections=_user_corrections(SLUG, EPISODE))
        assert ads_to_remove == []
    finally:
        db.delete_podcast(SLUG)


def test_reject_dropping_a_trimmed_confirm_restores_original_bounds(app_client):
    undecided = dict(HELD, start=840.0, end=1230.0)
    approved = dict(HELD, start=909.0, end=1229.0)
    db = _seed([undecided, approved])
    try:
        ref = {'db': db, 'client': app_client}
        assert _post(ref, {'type': 'confirm',
                           'original_ad': {'start': 909.0, 'end': 1229.0},
                           'adjusted_start': 920.0,
                           'adjusted_end': 1200.0}).status_code == 200
        assert _post(ref, {'type': 'reject',
                           'original_ad': {'start': 840.0, 'end': 1230.0}}).status_code == 200
        other = next(m for m in _markers(ref) if m.get('held_for_review'))
        assert not other.get('approved')
        assert (other['start'], other['end']) == (909.0, 1229.0)
        assert 'reviewer_original_start' not in other
    finally:
        db.delete_podcast(SLUG)
