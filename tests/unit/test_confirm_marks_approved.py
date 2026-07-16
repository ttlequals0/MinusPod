"""Approving a held marker annotates it server-side (issue #509): the marker
gains approved=True while staying pending until a recut applies it, so the
apply-bar count survives reloads and float drift (tolerance matching, like
the reject path) instead of joining on exact correction bounds."""
import json
import os
import tempfile
from unittest.mock import MagicMock

os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='confirm_held_test_'))
os.environ.setdefault('SECRET_KEY', 'test-secret')

from api.patterns import _handle_confirm_correction


def _seed(temp_db, markers, slug='confirm-test', episode_id='abcdef012345'):
    temp_db.create_podcast(slug, 'https://example.com/feed.xml', 'Confirm Test')
    temp_db.upsert_episode(slug=slug, episode_id=episode_id,
                           original_url='https://example.com/ep.mp3',
                           title='Test Episode', original_duration=3600.0)
    held_count = sum(1 for m in markers
                     if m.get('held_for_review') and not m.get('was_cut'))
    temp_db.save_episode_details(slug, episode_id, ad_markers=markers,
                                 pending_review_count=held_count)
    return slug, episode_id


def _held(start, end):
    return {'start': start, 'end': end, 'confidence': 0.95,
            'reason': 'sponsor read', 'was_cut': False,
            'held_for_review': True, 'hold_reason': 'max_duration',
            'validation': {'decision': 'REVIEW', 'adjusted_confidence': 0.95}}


def _markers(temp_db, slug, episode_id):
    ep = temp_db.get_episode(slug, episode_id)
    return json.loads(ep['ad_markers_json']), ep.get('pending_review_count', 0)


def _confirm(temp_db, slug, eid, start, end):
    from main_app import app
    with app.test_request_context():
        _handle_confirm_correction(
            temp_db, MagicMock(), slug, eid,
            {'start': start, 'end': end}, {},
        )


def test_confirm_marks_matching_held_marker_approved(temp_db):
    markers = [_held(100.0, 200.0), _held(300.0, 400.0)]
    slug, eid = _seed(temp_db, markers)

    _confirm(temp_db, slug, eid, 100.0, 200.0)

    saved, count = _markers(temp_db, slug, eid)
    approved = [m for m in saved if m.get('approved')]
    assert len(approved) == 1
    assert approved[0]['start'] == 100.0
    # Still pending until a recut applies it.
    assert approved[0]['held_for_review'] is True
    assert approved[0]['was_cut'] is False
    assert count == 2, "approval does not resolve the marker; count unchanged"


def test_confirm_matches_with_tolerance(temp_db):
    markers = [_held(100.0, 200.0)]
    slug, eid = _seed(temp_db, markers)

    _confirm(temp_db, slug, eid, 100.3, 199.8)

    saved, _ = _markers(temp_db, slug, eid)
    assert saved[0].get('approved') is True


def test_confirm_without_held_match_changes_nothing(temp_db):
    markers = [_held(100.0, 200.0)]
    slug, eid = _seed(temp_db, markers)

    _confirm(temp_db, slug, eid, 900.0, 950.0)

    saved, count = _markers(temp_db, slug, eid)
    assert 'approved' not in saved[0]
    assert count == 1


def _confirm_trimmed(temp_db, slug, eid, start, end, adj_start, adj_end):
    from main_app import app
    with app.test_request_context():
        _handle_confirm_correction(
            temp_db, MagicMock(), slug, eid,
            {'start': start, 'end': end},
            {'adjusted_start': adj_start, 'adjusted_end': adj_end},
        )


def test_confirm_trimmed_moves_marker_bounds_and_approves(temp_db):
    # Approving the reviewer's proposed trim: marker bounds move to the
    # trimmed span (recut then cuts only the ad portion), original bounds
    # kept for audit, marker approved but still pending until recut.
    markers = [_held(100.0, 200.0)]
    slug, eid = _seed(temp_db, markers)

    _confirm_trimmed(temp_db, slug, eid, 100.0, 200.0, 130.0, 200.0)

    saved, count = _markers(temp_db, slug, eid)
    m = saved[0]
    assert m['approved'] is True
    assert m['start'] == 130.0 and m['end'] == 200.0
    assert m['reviewer_original_start'] == 100.0
    assert m['reviewer_original_end'] == 200.0
    assert m['held_for_review'] is True
    assert m['was_cut'] is False
    assert count == 1

    # The stored correction row carries the trimmed bounds for audit.
    row = temp_db.get_connection().execute(
        "SELECT corrected_bounds FROM pattern_corrections WHERE episode_id = ?",
        (eid,),
    ).fetchone()
    cb = json.loads(row['corrected_bounds'])
    assert cb == {'start': 130.0, 'end': 200.0}

    # And the validator-facing accessor exposes it as confirmed_span so a
    # later reprocess clamps a re-detected wider span to the approved trim.
    corrections = temp_db.get_confirmed_corrections(eid)
    assert corrections[0]['start'] == 100.0 and corrections[0]['end'] == 200.0
    assert corrections[0]['confirmed_span'] == {'start': 130.0, 'end': 200.0}


def test_confirm_without_trim_keeps_marker_bounds(temp_db):
    markers = [_held(100.0, 200.0)]
    slug, eid = _seed(temp_db, markers)

    _confirm(temp_db, slug, eid, 100.0, 200.0)

    saved, _ = _markers(temp_db, slug, eid)
    m = saved[0]
    assert m['approved'] is True
    assert m['start'] == 100.0 and m['end'] == 200.0
    assert 'reviewer_original_start' not in m


def _confirm_raw(temp_db, slug, eid, original, data):
    from main_app import app
    with app.test_request_context():
        return _handle_confirm_correction(
            temp_db, MagicMock(), slug, eid, original, data,
        )


def _status(resp):
    # _handle_confirm_correction returns a Flask response (or (resp, code)).
    if isinstance(resp, tuple):
        return resp[1]
    return resp.status_code


class TestTrimmedConfirmValidation:
    def test_one_sided_trim_is_rejected(self, temp_db):
        markers = [_held(100.0, 200.0)]
        slug, eid = _seed(temp_db, markers)
        resp = _confirm_raw(temp_db, slug, eid,
                            {'start': 100.0, 'end': 200.0},
                            {'adjusted_start': 130.0})
        assert _status(resp) == 400
        saved, _ = _markers(temp_db, slug, eid)
        assert 'approved' not in saved[0]

    def test_inverted_trim_is_rejected(self, temp_db):
        markers = [_held(100.0, 200.0)]
        slug, eid = _seed(temp_db, markers)
        resp = _confirm_raw(temp_db, slug, eid,
                            {'start': 100.0, 'end': 200.0},
                            {'adjusted_start': 200.0, 'adjusted_end': 130.0})
        assert _status(resp) == 400

    def test_non_numeric_trim_is_rejected(self, temp_db):
        markers = [_held(100.0, 200.0)]
        slug, eid = _seed(temp_db, markers)
        resp = _confirm_raw(temp_db, slug, eid,
                            {'start': 100.0, 'end': 200.0},
                            {'adjusted_start': 'abc', 'adjusted_end': 200.0})
        assert _status(resp) == 400

    def test_out_of_span_trim_is_rejected(self, temp_db):
        markers = [_held(100.0, 200.0)]
        slug, eid = _seed(temp_db, markers)
        resp = _confirm_raw(temp_db, slug, eid,
                            {'start': 100.0, 'end': 200.0},
                            {'adjusted_start': 50.0, 'adjusted_end': 200.0})
        assert _status(resp) == 400
