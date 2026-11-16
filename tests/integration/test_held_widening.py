"""Widening a held marker: the 60 s window, its blockers and clamps, the payload."""
import json

import pytest

from api import get_database
from main_app import processing
from main_app.pipeline import recut
from tests.unit.pass2_test_utils import _user_corrections

SLUG = 'held-widen-feed'
EPISODE = 'd1e2f3a4b5c6'
DURATION = 9500.0


@pytest.fixture(autouse=True)
def _widen_sixty(monkeypatch):
    """These tests describe the widened window (held_widen_seconds = 60)."""
    monkeypatch.setenv('HELD_WIDEN_SECONDS', '60')


def _marker(start, end, **extra):
    m = {'start': start, 'end': end, 'confidence': 0.9, 'sponsor': 'Acme',
         'reason': 'span', 'category': 'sponsor', 'action_applied': 'remove',
         'detection_stage': 'first_pass', 'was_cut': False,
         'held_for_review': True, 'hold_reason': 'reviewer_boundary_conflict'}
    m.update(extra)
    return m


def _csrf(client):
    with client.session_transaction() as sess:
        sess['authenticated'] = True
    client.get('/api/v1/auth/status')
    cookie = client.get_cookie('minuspod_csrf')
    return {'X-CSRF-Token': cookie.value} if cookie else {}


@pytest.fixture
def seed(app_client):
    state = {}

    def _seed(markers, duration=DURATION):
        db = get_database()
        db.create_podcast(SLUG, 'https://example.com/feed.xml', 'Widen Show')
        db.upsert_episode(slug=SLUG, episode_id=EPISODE,
                          original_url='https://example.com/ep.mp3', title='Ep',
                          original_duration=duration, status='processed')
        db.save_episode_details(SLUG, EPISODE,
                                transcript_text='[00:00:00.000 --> 00:00:30.000] hi',
                                ad_markers=markers)
        conn = db.get_connection()
        conn.execute('DELETE FROM pattern_corrections WHERE episode_id = ?', (EPISODE,))
        conn.commit()
        state['db'] = db
        return {'db': db, 'client': app_client}

    yield _seed
    if 'db' in state:
        state['db'].delete_podcast(SLUG)


def _post(ref, body):
    c = ref['client']
    return c.post(f'/api/v1/episodes/{SLUG}/{EPISODE}/corrections',
                  json=body, headers=_csrf(c))


def _confirm(ref, orig, a, b):
    return _post(ref, {'type': 'confirm',
                       'original_ad': {'start': orig[0], 'end': orig[1]},
                       'adjusted_start': a, 'adjusted_end': b})


def _stored(ref):
    return json.loads(ref['db'].get_episode(SLUG, EPISODE)['ad_markers_json'])


def _payload(ref):
    c = ref['client']
    r = c.get(f'/api/v1/feeds/{SLUG}/episodes/{EPISODE}', headers=_csrf(c))
    assert r.status_code == 200
    return r.get_json()


def _bounds_for(ref, start, end):
    for m in _payload(ref)['pendingReviewMarkers']:
        if abs(m['start'] - start) < 0.01 and abs(m['end'] - end) < 0.01:
            return m['allowedBounds']
    raise AssertionError('marker not pending')


def test_confirm_widen_start_within_60s_accepted(seed):
    ref = seed([_marker(1000.0, 1100.0)])
    assert _confirm(ref, (1000.0, 1100.0), 945.0, 1100.0).status_code == 200
    assert _stored(ref)[0]['start'] == 945.0


def test_confirm_widen_end_within_60s_accepted(seed):
    ref = seed([_marker(1000.0, 1100.0)])
    assert _confirm(ref, (1000.0, 1100.0), 1000.0, 1159.0).status_code == 200
    assert _stored(ref)[0]['end'] == 1159.0


def test_confirm_widen_beyond_60s_returns_400(seed):
    ref = seed([_marker(1000.0, 1100.0)])
    assert _confirm(ref, (1000.0, 1100.0), 930.0, 1100.0).status_code == 400
    assert _confirm(ref, (1000.0, 1100.0), 1000.0, 1170.0).status_code == 400
    assert not _stored(ref)[0].get('approved')


def test_window_clamped_at_zero(seed):
    ref = seed([_marker(20.0, 120.0)])
    assert _confirm(ref, (20.0, 120.0), -0.3, 120.0).status_code == 400
    assert _confirm(ref, (20.0, 120.0), 0.0, 120.0).status_code == 200


def test_window_clamped_at_episode_duration(seed):
    ref = seed([_marker(1000.0, 1100.0)], duration=1130.0)
    assert _confirm(ref, (1000.0, 1100.0), 1000.0, 1130.4).status_code == 400
    assert _confirm(ref, (1000.0, 1100.0), 1000.0, 1130.0).status_code == 200


def test_window_stops_at_cut_neighbour(seed):
    cut = _marker(900.0, 980.0, was_cut=True, held_for_review=False)
    cut.pop('hold_reason')
    ref = seed([cut, _marker(1000.0, 1100.0)])
    assert _confirm(ref, (1000.0, 1100.0), 970.0, 1100.0).status_code == 400
    assert _confirm(ref, (1000.0, 1100.0), 980.0, 1100.0).status_code == 200


def test_window_stops_at_approved_neighbour(seed):
    ref = seed([_marker(900.0, 980.0, approved=True), _marker(1000.0, 1100.0)])
    assert _confirm(ref, (1000.0, 1100.0), 970.0, 1100.0).status_code == 400
    assert _confirm(ref, (1000.0, 1100.0), 981.0, 1100.0).status_code == 200


def test_window_ignores_undecided_held_neighbour(seed):
    ref = seed([_marker(900.0, 980.0), _marker(1000.0, 1100.0)])
    assert _confirm(ref, (1000.0, 1100.0), 960.0, 1100.0).status_code == 200


def test_reviewer_proposal_still_extends_window(seed):
    ref = seed([_marker(1000.0, 1100.0, reviewer_proposed_start=1000.0,
                        reviewer_proposed_end=1120.0)])
    # 1120 sits inside the 60 s window already; the proposal must not shrink it.
    assert _confirm(ref, (1000.0, 1100.0), 1000.0, 1120.0).status_code == 200


def test_reviewer_proposal_beyond_60s_grows_the_window(seed):
    ref = seed([_marker(1000.0, 1100.0, reviewer_proposed_start=1000.0,
                        reviewer_proposed_end=1200.0)])
    ref['db'].set_setting('review_max_boundary_shift', '120')
    # 1200 is past the +60 s window but within the reviewer's 120 s shift cap.
    try:
        assert _bounds_for(ref, 1000.0, 1100.0)['max'] == 1200.5
    except AssertionError:
        ref['db'].set_setting('review_max_boundary_shift', '60')
        raise
    try:
        assert _confirm(ref, (1000.0, 1100.0), 1000.0, 1200.0).status_code == 200
        assert _stored(ref)[0]['end'] == 1200.0
    finally:
        ref['db'].set_setting('review_max_boundary_shift', '60')


def test_reviewer_proposal_beyond_shift_cap_is_ignored(seed):
    ref = seed([_marker(1000.0, 1100.0, reviewer_proposed_start=1000.0,
                        reviewer_proposed_end=1200.0)])
    # Default cap is 60 s: the 100 s proposal is unusable and adds nothing.
    assert _bounds_for(ref, 1000.0, 1100.0)['max'] == 1160.5
    assert _confirm(ref, (1000.0, 1100.0), 1000.0, 1200.0).status_code == 400


def test_approved_trimmed_marker_can_grow_back_to_original_plus_60(seed):
    ref = seed([_marker(1000.0, 1100.0)])
    assert _confirm(ref, (1000.0, 1100.0), 1030.0, 1070.0).status_code == 200
    # Reopened: current span 1030-1070, first original 1000-1100.
    assert _confirm(ref, (1030.0, 1070.0), 930.0, 1070.0).status_code == 400
    assert _confirm(ref, (1030.0, 1070.0), 1000.0, 1170.0).status_code == 400
    assert _confirm(ref, (1030.0, 1070.0), 945.0, 1159.0).status_code == 200


def test_episode_payload_allowed_bounds_matches_enforced_window(seed):
    ref = seed([_marker(1000.0, 1100.0)])
    ab = _bounds_for(ref, 1000.0, 1100.0)
    assert ab == {'min': 939.5, 'max': 1160.5}
    assert _confirm(ref, (1000.0, 1100.0), ab['min'], ab['max']).status_code == 200


def test_payload_allowed_bounds_rejected_just_outside(seed):
    ref = seed([_marker(1000.0, 1100.0)])
    ab = _bounds_for(ref, 1000.0, 1100.0)
    assert _confirm(ref, (1000.0, 1100.0), ab['min'] - 0.1, 1100.0).status_code == 400
    assert _confirm(ref, (1000.0, 1100.0), 1000.0, ab['max'] + 0.1).status_code == 400


def test_window_stops_at_kept_neighbour(seed):
    kept = _marker(900.0, 980.0, action_applied='keep', held_for_review=False)
    kept.pop('hold_reason')
    ref = seed([kept, _marker(1000.0, 1100.0)])
    assert _confirm(ref, (1000.0, 1100.0), 975.0, 1100.0).status_code == 400
    assert _bounds_for(ref, 1000.0, 1100.0)['min'] == 980.0


def test_window_stops_at_user_rejected_span(seed):
    ref = seed([_marker(900.0, 980.0), _marker(1000.0, 1100.0)])
    assert _post(ref, {'type': 'reject',
                       'original_ad': {'start': 900.0, 'end': 980.0}}).status_code == 200
    assert _confirm(ref, (1000.0, 1100.0), 975.0, 1100.0).status_code == 400
    assert _confirm(ref, (1000.0, 1100.0), 980.0, 1100.0).status_code == 200


def test_window_stops_at_false_positive_correction_span(seed):
    ref = seed([_marker(1000.0, 1100.0)])
    db = ref['db']
    db.create_pattern_correction(
        correction_type='false_positive', pattern_id=None, episode_id=EPISODE,
        original_bounds={'start': 900.0, 'end': 980.0}, corrected_bounds=None,
        text_snippet=None, podcast_id=db.get_podcast_row(SLUG)['id'])
    assert _bounds_for(ref, 1000.0, 1100.0)['min'] == 980.0


def test_overlapping_approved_neighbour_does_not_shrink_below_current_span(seed):
    ref = seed([_marker(1050.0, 1200.0, approved=True), _marker(1000.0, 1100.0)])
    ab = _bounds_for(ref, 1000.0, 1100.0)
    assert ab['min'] <= 1000.0 and ab['max'] >= 1100.0
    assert _confirm(ref, (1000.0, 1100.0), 1001.0, 1099.0).status_code == 200


def test_window_at_episode_head_never_goes_negative(seed):
    ref = seed([_marker(5.0, 60.0)])
    assert _bounds_for(ref, 5.0, 60.0)['min'] == 0.0


def test_adjust_on_held_is_limited_by_the_same_window(seed):
    ref = seed([_marker(1000.0, 1100.0)])
    body = {'type': 'adjust', 'original_ad': {'start': 1000.0, 'end': 1100.0}}
    assert _post(ref, dict(body, adjusted_start=500.0,
                           adjusted_end=1900.0)).status_code == 400
    assert _post(ref, dict(body, adjusted_start=940.0,
                           adjusted_end=1160.0)).status_code == 200


def _recut_ads(ref, monkeypatch):
    monkeypatch.setattr(processing, 'db', ref['db'])
    ads_to_remove, *_ = recut._build_recut_ad_list(
        SLUG, EPISODE, [], DURATION, '', 0.80,
        corrections=_user_corrections(SLUG, EPISODE))
    return sorted((a['start'], a['end']) for a in ads_to_remove)


def test_recut_cuts_widened_span(seed, monkeypatch):
    ref = seed([_marker(9313.0, 9327.0)])
    assert _confirm(ref, (9313.0, 9327.0), 9313.0, 9387.0).status_code == 200
    assert _recut_ads(ref, monkeypatch) == [(9313.0, 9387.0)]


def test_recut_after_retrim_then_grow_back_cuts_latest_span(seed, monkeypatch):
    ref = seed([_marker(1000.0, 1100.0)])
    assert _confirm(ref, (1000.0, 1100.0), 1030.0, 1070.0).status_code == 200
    assert _confirm(ref, (1030.0, 1070.0), 990.0, 1140.0).status_code == 200
    assert _recut_ads(ref, monkeypatch) == [(990.0, 1140.0)]
