"""held_widen_seconds through the corrections API and the recut replay, at 0 (default) and at 60."""
import json
import logging

import pytest

from api import get_database
from main_app import processing
from main_app.pipeline import recut

SLUG = 'held-widen-setting-feed'
EPISODE = 'e1f2a3b4c5d6'
DURATION = 3600.0


def _held(start=1000.0, end=1100.0, **extra):
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
def ref(app_client):
    db = get_database()
    db.create_podcast(SLUG, 'https://example.com/feed.xml', 'Widen Setting Show')
    db.upsert_episode(slug=SLUG, episode_id=EPISODE,
                      original_url='https://example.com/ep.mp3', title='Ep',
                      original_duration=DURATION, status='processed')
    db.save_episode_details(SLUG, EPISODE,
                            transcript_text='[00:00:00.000 --> 00:00:30.000] hi',
                            ad_markers=[_held()])
    conn = db.get_connection()
    conn.execute('DELETE FROM pattern_corrections WHERE episode_id = ?', (EPISODE,))
    conn.commit()
    yield {'db': db, 'client': app_client}
    db.delete_podcast(SLUG)


def _post(ref, body):
    c = ref['client']
    return c.post(f'/api/v1/episodes/{SLUG}/{EPISODE}/corrections',
                  json=body, headers=_csrf(c))


def _adjust(ref, a, b):
    return _post(ref, {'type': 'adjust',
                       'original_ad': {'start': 1000.0, 'end': 1100.0},
                       'adjusted_start': a, 'adjusted_end': b})


def _confirm(ref, a, b):
    return _post(ref, {'type': 'confirm',
                       'original_ad': {'start': 1000.0, 'end': 1100.0},
                       'adjusted_start': a, 'adjusted_end': b})


def _marker(ref):
    return json.loads(ref['db'].get_episode(SLUG, EPISODE)['ad_markers_json'])[0]


def _allowed(ref):
    c = ref['client']
    r = c.get(f'/api/v1/feeds/{SLUG}/episodes/{EPISODE}', headers=_csrf(c))
    [m] = r.get_json()['pendingReviewMarkers']
    return m['allowedBounds']


class TestDefaultWindow:
    def test_confirm_trim_may_only_narrow(self, ref):
        assert _confirm(ref, 990.0, 1100.0).status_code == 400
        assert _confirm(ref, 1000.0, 1101.0).status_code == 400
        assert _confirm(ref, 1000.4, 1099.6).status_code == 200

    def test_editor_bounds_are_the_reviewed_span_with_tolerance(self, ref):
        assert _allowed(ref) == {'min': 999.5, 'max': 1100.5}

    def test_adjust_records_but_does_not_approve_or_move_the_marker(self, ref):
        assert _adjust(ref, 950.0, 1150.0).status_code == 200
        m = _marker(ref)
        assert (m['start'], m['end']) == (1000.0, 1100.0)
        assert 'approved' not in m
        assert m['held_for_review'] is True

    def test_adjust_is_not_limited_to_the_window(self, ref):
        assert _adjust(ref, 400.0, 1900.0).status_code == 200

    def test_adjust_still_rejects_a_reversed_or_tiny_span(self, ref):
        assert _adjust(ref, 1100.0, 1000.0).status_code == 400
        assert _adjust(ref, 1000.0, 1000.5).status_code == 400

    def test_legacy_replay_applies_an_adjustment_far_outside_the_reviewed_span(
            self, ref, monkeypatch):
        monkeypatch.setattr(processing, 'db', ref['db'])
        assert _adjust(ref, 940.0, 1160.0).status_code == 200
        ads = [_held()]
        recut._apply_boundary_adjustments(
            SLUG, EPISODE, ads, episode=ref['db'].get_episode(SLUG, EPISODE))
        assert (ads[0]['start'], ads[0]['end']) == (940.0, 1160.0)

    def test_legacy_replay_still_drops_a_sub_second_row(self, ref, monkeypatch):
        monkeypatch.setattr(processing, 'db', ref['db'])
        ref['db'].create_pattern_correction(
            correction_type='boundary_adjustment', pattern_id=None, episode_id=EPISODE,
            original_bounds={'start': 1000.0, 'end': 1100.0},
            corrected_bounds={'start': 1050.0, 'end': 1050.4}, text_snippet=None,
            podcast_id=ref['db'].get_podcast_row(SLUG)['id'])
        ads = [_held()]
        recut._apply_boundary_adjustments(
            SLUG, EPISODE, ads, episode=ref['db'].get_episode(SLUG, EPISODE))
        assert (ads[0]['start'], ads[0]['end']) == (1000.0, 1100.0)


@pytest.mark.usefixtures('held_widen_sixty')
class TestWidenedBehaviour:
    def test_confirm_trim_may_widen_to_sixty_seconds(self, ref):
        assert _confirm(ref, 940.0, 1160.0).status_code == 200
        assert _confirm(ref, 930.0, 1100.0).status_code == 400

    def test_editor_bounds_widen(self, ref):
        assert _allowed(ref) == {'min': 939.5, 'max': 1160.5}

    def test_adjust_approves_and_moves_the_marker_at_once(self, ref):
        assert _adjust(ref, 950.0, 1150.0).status_code == 200
        m = _marker(ref)
        assert (m['start'], m['end']) == (950.0, 1150.0)
        assert m['approved'] is True

    def test_adjust_beyond_the_window_returns_400(self, ref):
        assert _adjust(ref, 400.0, 1900.0).status_code == 400

    def test_replay_drops_an_out_of_window_row_with_a_warning_naming_it(
            self, ref, monkeypatch, caplog):
        monkeypatch.setattr(processing, 'db', ref['db'])
        db = ref['db']
        db.create_pattern_correction(
            correction_type='boundary_adjustment', pattern_id=None, episode_id=EPISODE,
            original_bounds={'start': 1000.0, 'end': 1100.0},
            corrected_bounds={'start': 700.0, 'end': 1100.0}, text_snippet=None,
            podcast_id=db.get_podcast_row(SLUG)['id'])
        row_id = db.get_connection().execute(
            'SELECT MAX(id) FROM pattern_corrections WHERE episode_id = ?',
            (EPISODE,)).fetchone()[0]
        ads = [_held()]
        caplog.set_level(logging.WARNING)
        recut._apply_boundary_adjustments(
            SLUG, EPISODE, ads, episode=db.get_episode(SLUG, EPISODE))
        assert (ads[0]['start'], ads[0]['end']) == (1000.0, 1100.0)
        warned = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any(f'(id {row_id})' in r.getMessage() for r in warned)
