"""Tests for the 2.95.2 _collapse_duplicate_ad_markers migration.

Pass 2 used to append a marker for a span pass 1 had already stored, so
existing episodes carry two dicts for one span. The migration folds only
pairs where both markers are uncut and both edges fall inside
BOUNDS_TOLERANCE_S; anything else is left exactly as it was.
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

GATE = 'collapse_duplicate_ad_markers'


def _seed(temp_db, markers, slug='collapse-test', episode_id='abcdef012345',
          pending_review_count=None):
    temp_db.create_podcast(slug, 'https://example.com/feed.xml', 'Collapse Test')
    temp_db.upsert_episode(slug=slug, episode_id=episode_id,
                           original_url='https://example.com/ep.mp3',
                           title='Test Episode', original_duration=3600.0)
    temp_db.save_episode_details(slug, episode_id, ad_markers=markers,
                                 pending_review_count=pending_review_count)
    return slug, episode_id


def _run(temp_db):
    """Clear the gate the boot-time run set, then run the migration."""
    conn = temp_db.get_connection()
    conn.execute("DELETE FROM schema_migrations WHERE name = ?", (GATE,))
    conn.commit()
    temp_db._collapse_duplicate_ad_markers(conn)
    return conn


def _stored(temp_db, slug, episode_id):
    ep = temp_db.get_episode(slug, episode_id)
    return json.loads(ep['ad_markers_json']), ep.get('pending_review_count')


def _keep(start=500.0, end=520.0):
    return {'start': start, 'end': end, 'category': 'recap', 'was_cut': False,
            'action_applied': 'keep', 'confidence': 0.9,
            'detection_stage': 'llm'}


def _held(start=500.2, end=519.8, reason='verification_kept_conflict'):
    return {'start': start, 'end': end, 'was_cut': False, 'sponsor': 'Acme',
            'held_for_review': True, 'hold_reason': reason,
            'validation': {'decision': 'REVIEW', 'flags': ['HOLD: kept span']}}


def test_a_duplicate_pair_collapses_and_keeps_the_richer_fields(temp_db):
    slug, eid = _seed(temp_db, [_keep(), _held()], pending_review_count=1)

    _run(temp_db)

    markers, pending = _stored(temp_db, slug, eid)
    assert len(markers) == 1
    marker = markers[0]
    assert marker['action_applied'] == 'keep'
    assert marker['was_cut'] is False
    assert 'held_for_review' not in marker
    assert marker['hold_cleared_reason'] == 'verification_kept_conflict'
    # Fields only the folded record carried survive.
    assert marker['sponsor'] == 'Acme'
    assert marker['category'] == 'recap'
    assert marker['validation']['flags'] == ['HOLD: kept span']
    assert pending == 0


def test_two_holds_for_one_span_stop_double_counting(temp_db):
    first = _held(500.0, 520.0, reason='max_duration')
    second = _held(500.2, 519.8, reason='cue_unproven')
    slug, eid = _seed(temp_db, [first, second], pending_review_count=2)

    _run(temp_db)

    markers, pending = _stored(temp_db, slug, eid)
    assert len(markers) == 1
    assert markers[0]['hold_reason'] == 'max_duration'
    assert ('INFO: Pass 2 also held this span (cue_unproven)'
            in markers[0]['validation']['flags'])
    assert pending == 1


def test_an_adjacent_pair_outside_tolerance_is_left_alone(temp_db):
    # 0.6s past the 0.5s both-edges tolerance on the end edge.
    slug, eid = _seed(temp_db, [_keep(), _held(500.2, 520.6)],
                      pending_review_count=1)

    _run(temp_db)

    markers, pending = _stored(temp_db, slug, eid)
    assert len(markers) == 2
    assert pending == 1


def test_a_pair_with_a_cut_marker_is_left_alone(temp_db):
    cut = dict(_keep(), was_cut=True, action_applied='remove')
    slug, eid = _seed(temp_db, [cut, _held()], pending_review_count=1)

    _run(temp_db)

    markers, _pending = _stored(temp_db, slug, eid)
    assert len(markers) == 2


def test_a_row_without_duplicates_is_not_rewritten(temp_db):
    slug, eid = _seed(temp_db, [_keep(), _held(900.0, 930.0)],
                      pending_review_count=1)
    conn = temp_db.get_connection()
    # Hand-formatted JSON: a rewrite would come back as json.dumps output.
    raw = '[ {"start": 500.0, "end": 520.0, "was_cut": false} ]'
    conn.execute("UPDATE episode_details SET ad_markers_json = ?", (raw,))
    conn.commit()

    _run(temp_db)

    row = conn.execute(
        "SELECT ad_markers_json FROM episode_details").fetchone()
    assert row['ad_markers_json'] == raw


def test_the_migration_runs_once(temp_db):
    slug, eid = _seed(temp_db, [_keep(), _held()], pending_review_count=1)

    conn = _run(temp_db)
    gate = conn.execute(
        "SELECT 1 FROM schema_migrations WHERE name = ?", (GATE,)).fetchone()
    assert gate is not None

    # A second call is a no-op even with a fresh duplicate in the row.
    temp_db.save_episode_details(slug, eid, ad_markers=[_keep(), _held()],
                                 pending_review_count=1)
    temp_db._collapse_duplicate_ad_markers(conn)

    markers, _pending = _stored(temp_db, slug, eid)
    assert len(markers) == 2
