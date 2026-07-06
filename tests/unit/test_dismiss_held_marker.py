"""Dismissing (rejecting) a held marker must clear its held state and recompute
pending_review_count so the amber chip and pendingReviewMarkers update on review."""
import json
import os
import tempfile

# main_app import (for the Flask app context) builds Storage from this env var.
os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='dismiss_held_test_'))
os.environ.setdefault('SECRET_KEY', 'test-secret')

from api.patterns import _handle_reject_correction


def _seed(temp_db, markers, slug='dismiss-test', episode_id='abcdef012345'):
    temp_db.create_podcast(slug, 'https://example.com/feed.xml', 'Dismiss Test')
    temp_db.upsert_episode(slug=slug, episode_id=episode_id,
                           original_url='https://example.com/ep.mp3',
                           title='Test Episode', original_duration=3600.0)
    held_count = sum(1 for m in markers
                     if m.get('held_for_review') and not m.get('was_cut'))
    temp_db.save_episode_details(slug, episode_id, ad_markers=markers,
                                 pending_review_count=held_count)
    return slug, episode_id


def _held(start, end, hold_reason='max_duration'):
    return {'start': start, 'end': end, 'confidence': 0.95,
            'reason': 'sponsor read', 'was_cut': False,
            'held_for_review': True, 'hold_reason': hold_reason,
            'validation': {'decision': 'REVIEW', 'adjusted_confidence': 0.95}}


def _split(temp_db, slug, episode_id):
    """Split markers the same way get_episode/api does."""
    ep = temp_db.get_episode(slug, episode_id)
    markers = json.loads(ep['ad_markers_json'])
    pending, rejected, accepted = [], [], []
    for m in markers:
        decision = m.get('validation', {}).get('decision', 'ACCEPT')
        was_cut = m.get('was_cut', True)
        held = m.get('held_for_review', False)
        if held and not was_cut:
            pending.append(m)
        elif decision == 'REJECT' or not was_cut:
            rejected.append(m)
        else:
            accepted.append(m)
    return pending, rejected, accepted, ep.get('pending_review_count', 0)


def test_dismiss_held_marker_clears_state_and_count(temp_db):
    from main_app import app
    markers = [_held(100.0, 200.0), _held(300.0, 400.0), _held(500.0, 600.0)]
    slug, eid = _seed(temp_db, markers)

    with app.test_request_context():
        _handle_reject_correction(temp_db, slug, eid,
                                  {'start': 300.0, 'end': 400.0})

    pending, rejected, _accepted, count = _split(temp_db, slug, eid)
    assert len(pending) == 2, "dismissed held marker must leave pending"
    assert len(rejected) == 1, "dismissed held marker must appear in rejected"
    assert count == 2, "pending_review_count must be recomputed"
    for m in rejected:
        assert not m.get('held_for_review')
        assert m.get('was_cut') is False
        assert m['validation']['decision'] == 'REJECT'


def test_dismiss_non_held_marker_leaves_others(temp_db):
    # Rejecting a range with no held match must not touch held markers/count.
    from main_app import app
    markers = [_held(100.0, 200.0)]
    slug, eid = _seed(temp_db, markers)
    with app.test_request_context():
        _handle_reject_correction(temp_db, slug, eid,
                                  {'start': 900.0, 'end': 950.0})
    pending, _rejected, _accepted, count = _split(temp_db, slug, eid)
    assert len(pending) == 1
    assert count == 1
