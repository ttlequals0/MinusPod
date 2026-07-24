"""Standalone pass-2 verification misses: the confidence-gate fall-through
that used to silently discard (`else: ad['was_cut'] = False`) now either
holds the ad for review or auto-cuts it, per verification_miss_* settings.

Drives `_gate_verification_ads_by_confidence` directly; these ads overlap no
pass-1 markers, so they exercise the new bucketing at the bottom of the loop.
"""
from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('pass2_standalone_misses_')
from config import HOLD_REASON_VERIFICATION_MISS, is_pending_review
from main_app.processing import _gate_verification_ads_by_confidence


def _proc(start, end, confidence):
    return {
        'start': start, 'end': end,
        'confidence': confidence,
        'validation': {'decision': 'REVIEW', 'adjusted_confidence': confidence},
    }


def _orig(start, end, confidence, sponsor='Acme'):
    return {
        'start': start, 'end': end,
        'confidence': confidence,
        'sponsor': sponsor,
        'reason': 'sponsor read',
    }


def test_miss_above_hold_floor_becomes_held_marker():
    """conf 0.8 with default settings (hold floor 0.60, autocut disabled)
    lands in v_ads_held, stamped verification_miss, and reads as pending
    review through the single source of truth."""
    proc = [_proc(100.0, 160.0, 0.8)]
    orig = [_orig(1100.0, 1160.0, 0.8)]

    v_ads_to_cut, v_ads_for_ui, v_ads_held, _n = _gate_verification_ads_by_confidence(
        proc, orig, min_cut_confidence=0.9,
    )

    assert v_ads_to_cut == []
    assert v_ads_for_ui == []
    assert len(v_ads_held) == 1
    marker = v_ads_held[0]
    assert marker['hold_reason'] == HOLD_REASON_VERIFICATION_MISS
    assert marker['detection_stage'] == 'verification_miss'
    assert marker.get('was_cut') is False
    assert marker.get('held_for_review') is True
    assert is_pending_review(marker) is True


def test_miss_below_hold_floor_is_discarded():
    """conf 0.5 is below the default 0.60 hold floor: discarded exactly as
    before, not held."""
    proc = [_proc(100.0, 160.0, 0.5)]
    orig = [_orig(1100.0, 1160.0, 0.5)]

    v_ads_to_cut, v_ads_for_ui, v_ads_held, _n = _gate_verification_ads_by_confidence(
        proc, orig, min_cut_confidence=0.9,
    )

    assert v_ads_to_cut == []
    assert v_ads_for_ui == []
    assert v_ads_held == []
    # Discard parity with the old fall-through: only the processed ad is
    # touched, same as before this task.
    assert proc[0].get('was_cut') is False
    assert not orig[0].get('held_for_review')


def test_miss_above_autocut_floor_is_cut_not_held():
    """With autocut enabled at 0.75 and conf 0.8, the ad routes into
    v_ads_to_cut (same as a gated cut ad) instead of v_ads_held."""
    proc = [_proc(100.0, 160.0, 0.8)]
    orig = [_orig(1100.0, 1160.0, 0.8)]

    v_ads_to_cut, v_ads_for_ui, v_ads_held, _n = _gate_verification_ads_by_confidence(
        proc, orig, min_cut_confidence=0.9,
        verification_miss_autocut_min_confidence=0.75,
    )

    assert v_ads_held == []
    assert len(v_ads_to_cut) == 1
    assert v_ads_to_cut[0].get('was_cut') is True
    assert v_ads_to_cut[0]['detection_stage'] == 'verification_miss'
    assert len(v_ads_for_ui) == 1
    assert v_ads_for_ui[0].get('was_cut') is True
    assert v_ads_for_ui[0]['detection_stage'] == 'verification_miss'


def test_autocut_disabled_by_default_falls_back_to_hold():
    """autocut floor 0 means disabled regardless of confidence; a miss that
    would clear a hypothetical autocut bar still only gets held."""
    proc = [_proc(100.0, 160.0, 0.99)]
    orig = [_orig(1100.0, 1160.0, 0.99)]

    v_ads_to_cut, _v_ads_for_ui, v_ads_held, _n = _gate_verification_ads_by_confidence(
        proc, orig, min_cut_confidence=0.999,
    )

    assert v_ads_to_cut == []
    assert len(v_ads_held) == 1


def test_miss_overlapping_pass1_held_marker_still_corroborates():
    """Regression pin: a miss overlapping a pass-1 held differential marker
    must still follow the existing corroboration path (_corroborates_hold),
    not the new standalone hold/autocut branch -- shaped like
    test_corroborating_ad_stamps_hold_and_is_still_dropped."""
    proc = [_proc(100.0, 249.0, 0.9)]
    orig = [_orig(4875.8, 5024.8, 0.9, sponsor='diff')]
    hold = {
        'start': 4875.8, 'end': 5025.8,
        'held_for_review': True, 'was_cut': False,
        'hold_reason': 'differential_uncorroborated',
        'differential_uncorroborated': True,
    }

    v_ads_to_cut, v_ads_for_ui, v_ads_held, n = _gate_verification_ads_by_confidence(
        proc, orig, min_cut_confidence=0.8, pass1_held_markers=[hold],
    )

    assert v_ads_to_cut == []
    assert v_ads_for_ui == []
    assert v_ads_held == []
    assert n == 1
    assert hold['pass2_corroborated'] is True
    assert orig[0].get('was_cut') is False
    # Must not be mistaken for a standalone verification-miss hold.
    assert orig[0].get('hold_reason') != HOLD_REASON_VERIFICATION_MISS
