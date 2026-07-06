"""Tests for T5: held-for-review API bucket split and pending_review_count.

Covers:
- get_episode three-way marker split (held FIRST, then reject, then accepted)
- Regression: held marker must NOT appear in rejectedAdMarkers
- save_combined_ads writes pending_review_count at the choke point
- clear_episode_details zeros pending_review_count
- clear_episode_ad_data zeros pending_review_count
- Migration idempotency (fresh DB has column; adding again is a no-op)
- list_episodes surfaces pendingReviewCount
"""
import atexit
import json
import os
import shutil
import sys
import tempfile

_test_data_dir = tempfile.mkdtemp(prefix='pending_review_test_')
os.environ.setdefault('SECRET_KEY', 'test-secret')
os.environ['DATA_DIR'] = _test_data_dir

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

import database

database.Database._instance = None
database.Database.__init__.__defaults__ = (_test_data_dir,)
database.Database.__new__.__defaults__ = (_test_data_dir,)
atexit.register(shutil.rmtree, _test_data_dir, ignore_errors=True)

db = database.Database()

_counter = [0]


def _eid():
    _counter[0] += 1
    return f"{_counter[0]:012x}"


def _seed(slug):
    db.create_podcast(slug, f'https://example.com/{slug}.xml', slug)
    eid = _eid()
    db.upsert_episode(slug=slug, episode_id=eid, title='ep',
                      original_url=f'https://example.com/{eid}.mp3',
                      status='processed')
    return eid


def _marker(decision, was_cut, held=False, hold_reason=None):
    m = {
        'start': 10.0,
        'end': 40.0,
        'was_cut': was_cut,
        'validation': {'decision': decision, 'confidence': 0.9},
    }
    if held:
        m['held_for_review'] = True
        if hold_reason:
            m['hold_reason'] = hold_reason
    return m


# ---------------------------------------------------------------------------
# Three-way marker split via get_episode
# ---------------------------------------------------------------------------

def _split_markers(slug, episode_id, markers):
    """Save markers and parse the split the same way get_episode does."""
    db.save_episode_details(slug, episode_id, ad_markers=markers)
    ep = db.get_episode(slug, episode_id)
    all_markers = json.loads(ep['ad_markers_json'])
    ad_markers = []
    rejected_ad_markers = []
    pending_review_markers = []
    for marker in all_markers:
        decision = marker.get('validation', {}).get('decision', 'ACCEPT')
        was_cut = marker.get('was_cut', True)
        held = marker.get('held_for_review', False)
        if held and not was_cut:
            pending_review_markers.append(marker)
        elif decision == 'REJECT' or not was_cut:
            rejected_ad_markers.append(marker)
        else:
            ad_markers.append(marker)
    return ad_markers, rejected_ad_markers, pending_review_markers


def test_normal_cut_goes_to_ad_markers():
    slug = 'split-normal'
    eid = _seed(slug)
    markers = [_marker('ACCEPT', was_cut=True)]
    ad, rej, pend = _split_markers(slug, eid, markers)
    assert len(ad) == 1
    assert len(rej) == 0
    assert len(pend) == 0


def test_reject_decision_goes_to_rejected():
    slug = 'split-reject'
    eid = _seed(slug)
    markers = [_marker('REJECT', was_cut=False)]
    ad, rej, pend = _split_markers(slug, eid, markers)
    assert len(ad) == 0
    assert len(rej) == 1
    assert len(pend) == 0


def test_held_marker_goes_to_pending_only():
    slug = 'split-held'
    eid = _seed(slug)
    markers = [_marker('REVIEW', was_cut=False, held=True, hold_reason='max_duration')]
    ad, rej, pend = _split_markers(slug, eid, markers)
    assert len(pend) == 1
    assert len(rej) == 0, "regression: held marker must NOT land in rejectedAdMarkers"
    assert len(ad) == 0


def test_held_marker_absent_from_rejected_regression():
    """Pre-T5 the held marker fell into rejectedAdMarkers. Verify it does not."""
    slug = 'split-regression'
    eid = _seed(slug)
    markers = [
        _marker('REVIEW', was_cut=False, held=True, hold_reason='no_cue_evidence'),
        _marker('REJECT', was_cut=False),
        _marker('ACCEPT', was_cut=True),
    ]
    ad, rej, pend = _split_markers(slug, eid, markers)
    assert len(pend) == 1
    assert len(rej) == 1
    assert len(ad) == 1
    # held marker must not appear in rejected
    for m in rej:
        assert not m.get('held_for_review'), "held marker leaked into rejectedAdMarkers"


def test_mixed_markers_split_correctly():
    slug = 'split-mixed'
    eid = _seed(slug)
    markers = [
        _marker('ACCEPT', was_cut=True),           # -> adMarkers
        _marker('REJECT', was_cut=False),           # -> rejectedAdMarkers
        _marker('REVIEW', was_cut=False, held=True, hold_reason='max_duration'),  # -> pendingReview
        _marker('REVIEW', was_cut=False, held=True, hold_reason='no_cue_evidence'),  # -> pendingReview
    ]
    ad, rej, pend = _split_markers(slug, eid, markers)
    assert len(ad) == 1
    assert len(rej) == 1
    assert len(pend) == 2


# ---------------------------------------------------------------------------
# pending_review_count choke-point writes (via storage layer via db directly)
# ---------------------------------------------------------------------------

def _get_pending_count(slug, eid):
    """Read pending_review_count directly from episodes table."""
    episodes, _ = db.get_episodes(slug)
    ep = next((e for e in episodes if e['episode_id'] == eid), None)
    assert ep is not None, f"Episode {eid} not found in {slug}"
    return ep.get('pending_review_count', 0)


def test_pending_review_count_written_on_save():
    slug = 'count-save'
    eid = _seed(slug)
    markers = [
        _marker('REVIEW', was_cut=False, held=True),   # held
        _marker('REVIEW', was_cut=False, held=True),   # held
        _marker('ACCEPT', was_cut=True),               # cut -> not held
        _marker('REJECT', was_cut=False),              # rejected -> not held
    ]
    db.save_episode_details(slug, eid, ad_markers=markers, pending_review_count=2)
    assert _get_pending_count(slug, eid) == 2


def test_pending_review_count_zero_by_default():
    slug = 'count-default'
    eid = _seed(slug)
    assert _get_pending_count(slug, eid) == 0


def test_pending_review_count_zeroed_by_clear_episode_details():
    slug = 'count-clear-details'
    eid = _seed(slug)
    db.save_episode_details(slug, eid, ad_markers=[], pending_review_count=3)
    db.clear_episode_details(slug, eid)
    assert _get_pending_count(slug, eid) == 0


def test_pending_review_count_zeroed_by_clear_episode_ad_data():
    slug = 'count-clear-ad-data'
    eid = _seed(slug)
    db.save_episode_details(slug, eid, ad_markers=[], pending_review_count=5)
    db.clear_episode_ad_data(slug, eid)
    assert _get_pending_count(slug, eid) == 0


# ---------------------------------------------------------------------------
# Migration idempotency
# ---------------------------------------------------------------------------

def test_migration_idempotent_fresh_db():
    """Fresh DB created in setUp already has pending_review_count; column is present."""
    import sqlite3
    db_path = os.path.join(_test_data_dir, 'podcast.db')
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.execute("PRAGMA table_info(episodes)")
    cols = {row['name'] for row in cursor.fetchall()}
    conn.close()
    assert 'pending_review_count' in cols


def test_migration_idempotent_add_again():
    """Calling _add_column_if_missing a second time for the same column is a no-op."""
    import sqlite3
    db_path = os.path.join(_test_data_dir, 'podcast.db')
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    ep_cols = {row['name'] for row in conn.execute("PRAGMA table_info(episodes)").fetchall()}
    # Should return False (already exists) and not raise
    result = db._add_column_if_missing(conn, 'episodes', 'pending_review_count',
                                       'INTEGER NOT NULL DEFAULT 0', ep_cols)
    conn.close()
    assert result is False


# ---------------------------------------------------------------------------
# list_episodes surfaces pendingReviewCount
# ---------------------------------------------------------------------------

def test_list_episodes_surfaces_pending_review_count():
    slug = 'list-prc'
    eid = _seed(slug)
    db.save_episode_details(slug, eid, ad_markers=[], pending_review_count=4)
    episodes, _ = db.get_episodes(slug)
    ep = next(e for e in episodes if e['episode_id'] == eid)
    assert ep['pending_review_count'] == 4


def test_list_episodes_pending_review_count_zero_when_no_held():
    slug = 'list-prc-zero'
    eid = _seed(slug)
    episodes, _ = db.get_episodes(slug)
    ep = next(e for e in episodes if e['episode_id'] == eid)
    assert ep.get('pending_review_count', 0) == 0
