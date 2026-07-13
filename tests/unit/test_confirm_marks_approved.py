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
