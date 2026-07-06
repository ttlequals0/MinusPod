"""Regression tests for processed/original twin pairing through AdValidator.

AdValidator.validate() sorts, merges and drops ads, so the old positional
keep_indices pairing could attach a surviving processed ad to the WRONG
original-coordinates twin (e.g. after A+B merge, C paired with B's original).
The fix carries each original through validation as a reference on the
processed dict.
"""
import atexit
import os
import shutil
import sys
import tempfile
from unittest.mock import MagicMock

# Boot pattern (see test_history_ad_count.py): bind a temp DATA_DIR before
# importing main_app, which otherwise mkdirs /app/data at module load.
_test_data_dir = tempfile.mkdtemp(prefix='validate_vads_test_')
os.environ.setdefault('SECRET_KEY', 'test-secret')
os.environ['DATA_DIR'] = _test_data_dir

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

import database
import storage as storage_mod

database.Database._instance = None
database.Database.__init__.__defaults__ = (_test_data_dir,)
database.Database.__new__.__defaults__ = (_test_data_dir,)
storage_mod.Storage.__init__.__defaults__ = (_test_data_dir,)

atexit.register(shutil.rmtree, _test_data_dir, ignore_errors=True)

from main_app.processing import _validate_verification_ads, _gate_verification_ads_by_confidence


def _seg(start, end, text='spoken content here'):
    return {'start': start, 'end': end, 'text': text}


def _db():
    db = MagicMock()
    db.get_false_positive_corrections.return_value = []
    return db


def _segments(duration=600.0):
    # Continuous speech so _merge_close_ads only merges sub-5s gaps, not
    # silent gaps up to MAX_SILENT_GAP.
    return [_seg(t, t + 30.0) for t in range(0, int(duration), 30)]


def _run(processed, original, duration=600.0):
    return _validate_verification_ads(
        'show', 'ep1', processed, original, _segments(duration),
        ads_to_remove=[], episode_description=None,
        min_cut_confidence=0.8, db=_db(),
    )


def test_merge_keeps_correct_twin_for_later_ad():
    # A and B sit 3s apart (< MERGE_GAP_THRESHOLD=5.0) and merge; C is far
    # away. Pre-fix, positional pairing matched merged-AB with A's original
    # and C with B's original. C must pair with C's own original.
    processed = [
        {'start': 100.0, 'end': 130.0, 'confidence': 0.9},
        {'start': 133.0, 'end': 160.0, 'confidence': 0.9},
        {'start': 400.0, 'end': 430.0, 'confidence': 0.9},
    ]
    original = [
        {'start': 1100.0, 'end': 1130.0, 'marker': 'A'},
        {'start': 1133.0, 'end': 1160.0, 'marker': 'B'},
        {'start': 1400.0, 'end': 1430.0, 'marker': 'C'},
    ]
    kept_proc, kept_orig = _run(processed, original)

    assert len(kept_proc) == len(kept_orig) == 2
    # Merged span keeps the surviving (first) ad's twin.
    assert kept_orig[0]['marker'] == 'A'
    # The far ad pairs with its own original, not B's.
    assert kept_proc[1]['start'] == 400.0
    assert kept_orig[1]['marker'] == 'C'


def test_unsorted_input_pairs_by_identity_not_position():
    # validate() sorts by start; positional pairing against the unsorted
    # original list would swap the twins.
    processed = [
        {'start': 400.0, 'end': 430.0, 'confidence': 0.9},
        {'start': 100.0, 'end': 130.0, 'confidence': 0.9},
    ]
    original = [
        {'start': 1400.0, 'end': 1430.0, 'marker': 'late'},
        {'start': 1100.0, 'end': 1130.0, 'marker': 'early'},
    ]
    kept_proc, kept_orig = _run(processed, original)

    assert len(kept_proc) == len(kept_orig) == 2
    by_start = dict(zip([p['start'] for p in kept_proc],
                        [o['marker'] for o in kept_orig]))
    assert by_start[100.0] == 'early'
    assert by_start[400.0] == 'late'


def test_invalid_ad_drops_with_its_twin():
    # end <= start is dropped by validate(); its twin must drop too and the
    # lists stay equal length.
    processed = [
        {'start': 130.0, 'end': 130.0, 'confidence': 0.9},
        {'start': 400.0, 'end': 430.0, 'confidence': 0.9},
    ]
    original = [
        {'start': 1130.0, 'end': 1130.0, 'marker': 'degenerate'},
        {'start': 1400.0, 'end': 1430.0, 'marker': 'good'},
    ]
    kept_proc, kept_orig = _run(processed, original)

    assert len(kept_proc) == len(kept_orig) == 1
    assert kept_orig[0]['marker'] == 'good'


def test_pairing_key_never_leaks_into_outputs():
    processed = [
        {'start': 100.0, 'end': 130.0, 'confidence': 0.9},
        {'start': 400.0, 'end': 430.0, 'confidence': 0.9},
    ]
    original = [
        {'start': 1100.0, 'end': 1130.0},
        {'start': 1400.0, 'end': 1430.0},
    ]
    kept_proc, kept_orig = _run(processed, original)

    for ad in kept_proc + kept_orig + processed + original:
        assert '_orig_twin' not in ad


def test_explicit_duration_is_validator_clamp_target():
    # Real file duration (520) is shorter than the last transcript segment
    # end (600): an ad overrunning the file must clamp to the real duration.
    processed = [{'start': 500.0, 'end': 590.0, 'confidence': 0.9}]
    original = [{'start': 1500.0, 'end': 1590.0, 'marker': 'tail'}]
    kept_proc, kept_orig = _validate_verification_ads(
        'show', 'ep1', processed, original, _segments(600.0),
        ads_to_remove=[], episode_description=None,
        min_cut_confidence=0.8, db=_db(), processed_duration=520.0,
    )
    assert len(kept_proc) == 1
    assert kept_proc[0]['end'] <= 520.0


def test_missing_duration_falls_back_to_last_segment_end():
    processed = [{'start': 500.0, 'end': 590.0, 'confidence': 0.9}]
    original = [{'start': 1500.0, 'end': 1590.0}]
    kept_proc, _ = _validate_verification_ads(
        'show', 'ep1', processed, original, _segments(600.0),
        ads_to_remove=[], episode_description=None,
        min_cut_confidence=0.8, db=_db(),
    )
    assert len(kept_proc) == 1
    assert kept_proc[0]['end'] <= 600.0


# ---------- Pass-2 gate held-for-review diversion ----------


def _held_proc(start, end, confidence=0.92, hold_reason='max_duration'):
    """Processed-coord ad with held_for_review set (as the validator would emit)."""
    return {
        'start': start, 'end': end,
        'confidence': confidence,
        'held_for_review': True,
        'hold_reason': hold_reason,
        'validation': {
            'decision': 'REVIEW',
            'adjusted_confidence': confidence,
        },
    }


def _plain_proc(start, end, confidence=0.92):
    """Processed-coord ad, not held."""
    return {
        'start': start, 'end': end,
        'confidence': confidence,
        'validation': {
            'decision': 'ACCEPT',
            'adjusted_confidence': confidence,
        },
    }


def _orig(start, end, marker='x'):
    return {'start': start, 'end': end, 'marker': marker}


def test_held_pass2_ad_diverts_to_v_ads_held():
    """A held pass-2 ad must land in v_ads_held, NOT in v_ads_for_ui or v_ads_to_cut."""
    proc = [_held_proc(100.0, 160.0)]
    orig = [_orig(1100.0, 1160.0, 'held')]

    v_ads_to_cut, v_ads_for_ui, v_ads_held = _gate_verification_ads_by_confidence(
        proc, orig, min_cut_confidence=0.8,
    )

    assert v_ads_to_cut == []
    assert v_ads_for_ui == []
    assert len(v_ads_held) == 1
    held = v_ads_held[0]
    # Must be the original-coord twin
    assert held['marker'] == 'held'
    # was_cut must be False
    assert held.get('was_cut') is False
    # held fields must be propagated from the processed ad
    assert held.get('held_for_review') is True
    assert held.get('hold_reason') == 'max_duration'


def test_non_held_pass2_ad_behavior_unchanged():
    """A plain above-threshold pass-2 ad still goes to v_ads_to_cut and v_ads_for_ui;
    v_ads_held is empty. Byte-identical behavior when no held ads present."""
    proc = [_plain_proc(100.0, 160.0)]
    orig = [_orig(1100.0, 1160.0, 'plain')]

    v_ads_to_cut, v_ads_for_ui, v_ads_held = _gate_verification_ads_by_confidence(
        proc, orig, min_cut_confidence=0.8,
    )

    assert v_ads_held == []
    assert len(v_ads_to_cut) == 1
    assert len(v_ads_for_ui) == 1
    assert v_ads_for_ui[0]['marker'] == 'plain'
    assert v_ads_for_ui[0].get('was_cut') is True


def test_held_not_in_v_ads_for_ui():
    """v_ads_for_ui feeds the reviewer accepted pool and asset mapping;
    held ads must never appear there."""
    proc = [_held_proc(100.0, 160.0), _plain_proc(300.0, 360.0)]
    orig = [_orig(1100.0, 1160.0, 'held'), _orig(1300.0, 1360.0, 'plain')]

    _v_ads_to_cut, v_ads_for_ui, v_ads_held = _gate_verification_ads_by_confidence(
        proc, orig, min_cut_confidence=0.8,
    )

    ui_markers = [a['marker'] for a in v_ads_for_ui]
    assert 'held' not in ui_markers
    assert 'plain' in ui_markers
    held_markers = [a['marker'] for a in v_ads_held]
    assert 'held' in held_markers


def test_held_was_cut_is_false():
    """The original-coord twin in v_ads_held must have was_cut=False."""
    proc = [_held_proc(200.0, 280.0, confidence=0.95, hold_reason='no_cue_evidence')]
    orig = [_orig(1200.0, 1280.0, 'nocue')]

    _, _, v_ads_held = _gate_verification_ads_by_confidence(
        proc, orig, min_cut_confidence=0.8,
    )

    assert len(v_ads_held) == 1
    assert v_ads_held[0].get('was_cut') is False
    assert v_ads_held[0].get('hold_reason') == 'no_cue_evidence'


def test_below_threshold_non_held_not_in_v_ads_held():
    """A below-threshold REVIEW that is NOT held should not go to v_ads_held;
    it stays in neither cut list (was_cut=False), and v_ads_held is empty."""
    proc = [{
        'start': 100.0, 'end': 160.0,
        'confidence': 0.5,
        'validation': {'decision': 'REVIEW', 'adjusted_confidence': 0.5},
    }]
    orig = [_orig(1100.0, 1160.0, 'lowconf')]

    v_ads_to_cut, v_ads_for_ui, v_ads_held = _gate_verification_ads_by_confidence(
        proc, orig, min_cut_confidence=0.8,
    )

    assert v_ads_to_cut == []
    assert v_ads_for_ui == []
    assert v_ads_held == []
