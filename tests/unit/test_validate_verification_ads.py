"""Regression tests for processed/original twin pairing through AdValidator.

AdValidator.validate() sorts, merges and drops ads, so the old positional
keep_indices pairing could attach a surviving processed ad to the WRONG
original-coordinates twin (e.g. after A+B merge, C paired with B's original).
The fix carries each original through validation as a reference on the
processed dict.
"""
from unittest.mock import MagicMock

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('validate_vads_test_')
from main_app.processing import (
    _drop_uncovered_pass2_ads,
    _gate_verification_ads_by_confidence,
    _validate_verification_ads,
)
import main_app.processing as processing_mod


def _seg(start, end, text='spoken content here'):
    return {'start': start, 'end': end, 'text': text}


def _db():
    db = MagicMock()
    db.get_false_positive_corrections.return_value = []
    db.get_confirmed_corrections.return_value = []
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


def _held_marker(start, end, hold_reason='no_cue_evidence'):
    return {'start': start, 'end': end, 'held_for_review': True,
            'was_cut': False, 'hold_reason': hold_reason}


def test_held_pass2_ad_diverts_to_v_ads_held():
    """A held pass-2 ad must land in v_ads_held, NOT in v_ads_for_ui or v_ads_to_cut."""
    proc = [_held_proc(100.0, 160.0)]
    orig = [_orig(1100.0, 1160.0, 'held')]

    v_ads_to_cut, v_ads_for_ui, v_ads_held, _n = _gate_verification_ads_by_confidence(
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

    v_ads_to_cut, v_ads_for_ui, v_ads_held, _n = _gate_verification_ads_by_confidence(
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

    _v_ads_to_cut, v_ads_for_ui, v_ads_held, _n = _gate_verification_ads_by_confidence(
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

    _, _, v_ads_held, _n = _gate_verification_ads_by_confidence(
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

    v_ads_to_cut, v_ads_for_ui, v_ads_held, _n = _gate_verification_ads_by_confidence(
        proc, orig, min_cut_confidence=0.8,
    )

    assert v_ads_to_cut == []
    assert v_ads_for_ui == []
    assert v_ads_held == []


# ---------- Pass-2 cut inside a pass-1 held span ----------


def test_pass2_cut_inside_pass1_held_span_is_dropped():
    """A pass-2 ad whose original span overlaps a pass-1 held span must be dropped
    (never cut, never re-held) -- the pass-1 held marker already protects the
    region, so adding a second held marker would double-count the review."""
    proc = [_plain_proc(100.0, 160.0)]  # would-be cut
    orig = [_orig(120.0, 250.0, 'inside')]  # original coords overlap held 100-500
    pass1_held = [_held_marker(100.0, 500.0)]

    v_ads_to_cut, v_ads_for_ui, v_ads_held, _n = _gate_verification_ads_by_confidence(
        proc, orig, min_cut_confidence=0.8, pass1_held_markers=pass1_held,
    )

    assert v_ads_to_cut == [], "Ad inside a held span must not be cut"
    assert v_ads_for_ui == [], "Diverted ad must not enter the UI/reviewer pool"
    assert v_ads_held == [], "Overlapping ad is dropped, not re-held (no double-count)"
    assert orig[0].get('was_cut') is False


def test_pass2_cut_outside_pass1_held_span_still_cut():
    """A pass-2 ad not overlapping any held span cuts normally."""
    proc = [_plain_proc(100.0, 160.0)]
    orig = [_orig(600.0, 660.0, 'outside')]
    pass1_held = [_held_marker(100.0, 500.0)]

    v_ads_to_cut, v_ads_for_ui, _v_ads_held, _n = _gate_verification_ads_by_confidence(
        proc, orig, min_cut_confidence=0.8, pass1_held_markers=pass1_held,
    )

    assert len(v_ads_to_cut) == 1
    assert v_ads_for_ui[0]['marker'] == 'outside'

# ---------- Differential-hold corroboration (pass-2 auto-approve) ----------


def _diff_hold(start, end):
    return {'start': start, 'end': end, 'held_for_review': True,
            'was_cut': False, 'hold_reason': 'differential_uncorroborated',
            'differential_uncorroborated': True}


def test_corroborating_ad_stamps_hold_and_is_still_dropped():
    """A confident pass-2 ad mostly inside a differential hold, covering
    nearly all of it, stamps the hold for auto-approval. The ad itself is
    still dropped: pending audio is never cut mid-pipeline."""
    proc = [_plain_proc(100.0, 249.0)]
    orig = [_orig(4875.8, 5024.8, 'diff')]
    hold = _diff_hold(4875.8, 5025.8)

    v_ads_to_cut, v_ads_for_ui, v_ads_held, n = _gate_verification_ads_by_confidence(
        proc, orig, min_cut_confidence=0.8, pass1_held_markers=[hold],
    )

    assert v_ads_to_cut == []
    assert v_ads_for_ui == []
    assert v_ads_held == []
    assert n == 1
    assert hold['pass2_corroborated'] is True
    assert any('Pass-2' in f for f in hold['validation']['flags'])
    # The hold itself must remain pending until the auto-approve recut.
    assert hold['held_for_review'] is True
    assert hold['was_cut'] is False
    assert orig[0].get('was_cut') is False


def test_short_ad_in_long_hold_does_not_stamp():
    """A 15s detection inside a 300s hold covers too little of it."""
    proc = [_plain_proc(100.0, 115.0)]
    orig = [_orig(1100.0, 1115.0, 'short')]
    hold = _diff_hold(1000.0, 1300.0)

    _cut, _ui, _held, n = _gate_verification_ads_by_confidence(
        proc, orig, min_cut_confidence=0.8, pass1_held_markers=[hold],
    )

    assert n == 0
    assert 'pass2_corroborated' not in hold


def test_graze_does_not_stamp():
    proc = [_plain_proc(100.0, 200.0)]
    orig = [_orig(4400.0, 4500.0, 'graze')]
    hold = _diff_hold(4480.0, 4600.0)  # 20s of the 100s ad inside

    _cut, _ui, _held, n = _gate_verification_ads_by_confidence(
        proc, orig, min_cut_confidence=0.8, pass1_held_markers=[hold],
    )

    assert n == 0
    assert 'pass2_corroborated' not in hold


def test_low_confidence_does_not_stamp():
    proc = [_plain_proc(990.0, 1150.0, confidence=0.5)]
    orig = [_orig(990.0, 1150.0, 'lowconf')]
    hold = _diff_hold(990.0, 1150.0)

    _cut, _ui, _held, n = _gate_verification_ads_by_confidence(
        proc, orig, min_cut_confidence=0.8, pass1_held_markers=[hold],
    )

    assert n == 0
    assert 'pass2_corroborated' not in hold


def test_non_differential_hold_never_stamped():
    proc = [_plain_proc(990.0, 1150.0)]
    orig = [_orig(990.0, 1150.0, 'contra')]
    hold = _held_marker(990.0, 1150.0, hold_reason='reviewer_contradiction')

    v_ads_to_cut, _ui, _held, n = _gate_verification_ads_by_confidence(
        proc, orig, min_cut_confidence=0.8, pass1_held_markers=[hold],
    )

    assert v_ads_to_cut == []
    assert n == 0
    assert 'pass2_corroborated' not in hold


def test_ad_overlapping_two_pending_markers_does_not_stamp():
    proc = [_plain_proc(1000.0, 1160.0)]
    orig = [_orig(1000.0, 1160.0, 'twohold')]
    hold = _diff_hold(1000.0, 1150.0)
    other = _held_marker(1140.0, 1200.0, hold_reason='no_cue_evidence')

    _cut, _ui, _held, n = _gate_verification_ads_by_confidence(
        proc, orig, min_cut_confidence=0.8, pass1_held_markers=[hold, other],
    )

    assert n == 0
    assert 'pass2_corroborated' not in hold


def test_two_corroborating_ads_stamp_once():
    proc = [_plain_proc(100.0, 240.0), _plain_proc(100.0, 245.0)]
    orig = [_orig(1000.0, 1140.0, 'a'), _orig(1000.0, 1145.0, 'b')]
    hold = _diff_hold(1000.0, 1150.0)

    _cut, _ui, _held, n = _gate_verification_ads_by_confidence(
        proc, orig, min_cut_confidence=0.8, pass1_held_markers=[hold],
    )

    assert n == 1
    assert hold['pass2_corroborated'] is True


def test_held_pass2_ad_still_diverts_never_stamps():
    proc = [_held_proc(990.0, 1150.0, hold_reason='no_cue_evidence')]
    orig = [_orig(990.0, 1150.0, 'heldover')]
    hold = _diff_hold(990.0, 1150.0)

    v_ads_to_cut, _ui, v_ads_held, n = _gate_verification_ads_by_confidence(
        proc, orig, min_cut_confidence=0.8, pass1_held_markers=[hold],
    )

    assert v_ads_to_cut == []
    assert len(v_ads_held) == 1
    assert n == 0
    assert 'pass2_corroborated' not in hold


def test_auto_approve_files_correction_and_recuts(monkeypatch):
    """The auto-approve helper writes the same confirm correction the approve
    button writes, then runs the standard recut once."""
    db = MagicMock()
    db.get_false_positive_corrections.return_value = []
    db.get_confirmed_corrections.return_value = []
    db.get_original_segments.return_value = [{'start': 0.0, 'end': 30.0}]
    recut = MagicMock(return_value=True)
    monkeypatch.setattr(processing_mod, 'db', db)
    monkeypatch.setattr(processing_mod, '_recut_episode', recut)
    monkeypatch.setattr(processing_mod, 'storage', MagicMock())

    hold = _diff_hold(4875.8, 5025.8)
    hold['pass2_corroborated'] = True
    other_pending = _held_marker(100.0, 160.0)  # not corroborated
    cut_marker = {'start': 0.0, 'end': 29.0, 'was_cut': True}

    n = processing_mod._auto_approve_corroborated_holds(
        's', 'ep1', 'Title', 'Pod', 'desc',
        [cut_marker, other_pending, hold])

    assert n == 1
    kwargs = db.create_pattern_correction.call_args.kwargs
    assert kwargs['correction_type'] == 'confirm'
    assert kwargs['original_bounds'] == {'start': 4875.8, 'end': 5025.8}
    assert kwargs['corrected_bounds'] is None
    recut.assert_called_once()
    assert recut.call_args.args[0] == 's'
    assert recut.call_args.args[1] == 'ep1'


def test_auto_approve_noop_without_stamped_holds(monkeypatch):
    db = MagicMock()
    recut = MagicMock()
    monkeypatch.setattr(processing_mod, 'db', db)
    monkeypatch.setattr(processing_mod, '_recut_episode', recut)

    n = processing_mod._auto_approve_corroborated_holds(
        's', 'ep1', 'Title', 'Pod', 'desc',
        [_diff_hold(1.0, 50.0), _held_marker(100.0, 160.0)])

    assert n == 0
    db.create_pattern_correction.assert_not_called()
    recut.assert_not_called()


def test_auto_approve_swallows_recut_failure(monkeypatch):
    """A recut failure must not raise: the episode is already complete and
    the hold stays pending for manual approval."""
    db = MagicMock()
    db.get_false_positive_corrections.return_value = []
    db.get_confirmed_corrections.return_value = []
    db.get_original_segments.return_value = [{'start': 0.0, 'end': 30.0}]
    monkeypatch.setattr(processing_mod, 'db', db)
    monkeypatch.setattr(processing_mod, 'storage', MagicMock())
    monkeypatch.setattr(processing_mod, '_recut_episode',
                        MagicMock(side_effect=RuntimeError('boom')))

    hold = _diff_hold(4875.8, 5025.8)
    hold['pass2_corroborated'] = True

    n = processing_mod._auto_approve_corroborated_holds(
        's', 'ep1', 'Title', 'Pod', 'desc', [hold])

    assert n == 0
    assert hold['held_for_review'] is True
    assert hold['was_cut'] is False


def test_drop_uncovered_handles_twinless_cut():
    """_drop_uncovered_pass2_ads must tolerate a cut dict with no id-twin in
    the processed/original map."""
    covered = {'start': 100.0, 'end': 150.0}
    filtered = {'start': 300.0, 'end': 305.0}
    v_ads_to_cut = [covered, filtered]

    _drop_uncovered_pass2_ads(
        's', 'e', v_ads_to_cut, [], [{'start': 100.0, 'end': 150.0}],
        [], [], total_duration=600.0,
    )

    assert v_ads_to_cut == [covered]
    assert filtered['was_cut'] is False


def test_auto_approve_respects_human_reject(monkeypatch):
    """A span the user explicitly rejected must never be auto-approved."""
    db = MagicMock()
    db.get_false_positive_corrections.return_value = [
        {'start': 4880.0, 'end': 5020.0}]
    db.get_confirmed_corrections.return_value = []
    db.get_original_segments.return_value = [{'start': 0.0, 'end': 30.0}]
    recut = MagicMock(return_value=True)
    monkeypatch.setattr(processing_mod, 'db', db)
    monkeypatch.setattr(processing_mod, '_recut_episode', recut)
    monkeypatch.setattr(processing_mod, 'storage', MagicMock())

    hold = _diff_hold(4875.8, 5025.8)
    hold['pass2_corroborated'] = True

    n = processing_mod._auto_approve_corroborated_holds(
        's', 'ep1', 'Title', 'Pod', 'desc', [hold])

    assert n == 0
    db.create_pattern_correction.assert_not_called()
    recut.assert_not_called()
    assert hold['held_for_review'] is True

def test_auto_approve_skips_without_retained_original(monkeypatch):
    """Missing retained original: no correction, no recut, no FAILED flip."""
    db = MagicMock()
    recut = MagicMock()
    storage = MagicMock()
    storage.get_original_path.return_value.exists.return_value = False
    monkeypatch.setattr(processing_mod, 'db', db)
    monkeypatch.setattr(processing_mod, 'storage', storage)
    monkeypatch.setattr(processing_mod, '_recut_episode', recut)

    hold = _diff_hold(4875.8, 5025.8)
    hold['pass2_corroborated'] = True

    n = processing_mod._auto_approve_corroborated_holds(
        's', 'ep1', 'Title', 'Pod', 'desc', [hold])

    assert n == 0
    db.create_pattern_correction.assert_not_called()
    recut.assert_not_called()


def test_auto_approve_dedupes_existing_confirm(monkeypatch):
    """An equivalent confirm already on file must not get a second row, but
    the recut still runs to apply it."""
    db = MagicMock()
    db.get_false_positive_corrections.return_value = []
    db.get_confirmed_corrections.return_value = [
        {'start': 4875.8, 'end': 5025.8}]
    db.get_original_segments.return_value = [{'start': 0.0, 'end': 30.0}]
    recut = MagicMock(return_value=True)
    monkeypatch.setattr(processing_mod, 'db', db)
    monkeypatch.setattr(processing_mod, 'storage', MagicMock())
    monkeypatch.setattr(processing_mod, '_recut_episode', recut)

    hold = _diff_hold(4875.8, 5025.8)
    hold['pass2_corroborated'] = True

    n = processing_mod._auto_approve_corroborated_holds(
        's', 'ep1', 'Title', 'Pod', 'desc', [hold])

    assert n == 1
    db.create_pattern_correction.assert_not_called()
    recut.assert_called_once()


def test_auto_approve_recut_gets_no_cancel_event(monkeypatch):
    """The recut must run with cancel_event=None: a cancel would propagate to
    the background wrapper's cleanup and delete the completed episode."""
    db = MagicMock()
    db.get_false_positive_corrections.return_value = []
    db.get_confirmed_corrections.return_value = []
    db.get_original_segments.return_value = [{'start': 0.0, 'end': 30.0}]
    recut = MagicMock(return_value=True)
    monkeypatch.setattr(processing_mod, 'db', db)
    monkeypatch.setattr(processing_mod, 'storage', MagicMock())
    monkeypatch.setattr(processing_mod, '_recut_episode', recut)

    hold = _diff_hold(4875.8, 5025.8)
    hold['pass2_corroborated'] = True

    processing_mod._auto_approve_corroborated_holds(
        's', 'ep1', 'Title', 'Pod', 'desc', [hold])

    assert recut.call_args.kwargs.get('cancel_event') is None
