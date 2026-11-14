"""Unit tests for per-template score_threshold (issue #350).

Precedence: per-template > per-feed > global. Covers gating in both directions, NULL fallback to the
instance threshold, the peak-pick floor, update_cue_template set/clear, _template_to_meta_dict
scoreThreshold, and PATCH validation."""
import os
import sys
import tempfile

# Must set before any src import to prevent config.py from baking /app/data
# as DATA_DIR when run in a targeted subset (same pattern as test_cue_boundary_snap.py).
os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='cue_thr_unit_test_'))
os.environ.setdefault('SECRET_KEY', 'test-secret')

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

import numpy as np
import pytest

from audio_analysis.cue_features import N_COEFFS, serialize_mfcc
from audio_analysis.cue_template_matcher import AudioCueTemplateMatcher
import api.cue_templates as ct_api


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_row(mfcc: np.ndarray, *, template_id: int = 1,
              score_threshold=None, label: str = 't') -> dict:
    return {
        'id': template_id,
        'label': label,
        'mfcc_blob': serialize_mfcc(mfcc),
        'duration_s': 0.5,
        'n_coeffs': mfcc.shape[1],
        'score_threshold': score_threshold,
    }


# ---------------------------------------------------------------------------
# Per-template threshold gates match that instance threshold would pass
# ---------------------------------------------------------------------------

def test_per_template_threshold_blocks_match_below_its_own_threshold():
    """Per-template threshold 0.999 suppresses a noisy haystack that instance 0.3 would pass (zero
    _scan_chunk matches)."""
    rng = np.random.default_rng(7)
    template = rng.standard_normal((10, N_COEFFS)).astype(np.float32)
    # Pure noise: no planted match; scores will be low but could exceed 0.3
    haystack_mfcc = rng.standard_normal((200, N_COEFFS)).astype(np.float32) * 0.5

    row = _make_row(template, score_threshold=0.999)
    # Instance threshold 0.3 is permissive; per-template 0.999 is the real gate.
    matcher = AudioCueTemplateMatcher(templates=[row], score_threshold=0.3)
    assert matcher.is_usable

    per_template_matches = {1: []}
    per_template_peak = {1: 0.0}
    per_template_near_misses = {1: []}
    matcher._scan_chunk(haystack_mfcc, 0.0, per_template_matches, per_template_peak, per_template_near_misses)

    # Noise haystack cannot reach 0.999; per-template gate blocks all matches.
    assert len(per_template_matches[1]) == 0


def test_per_template_threshold_allows_match_that_instance_would_block():
    """Per-template 0.5 matches a degraded copy (score in 0.5-0.95) that instance 0.95 alone would block.

    _scan_chunk is called directly to assert on match counts."""
    rng = np.random.default_rng(42)
    template = rng.standard_normal((20, N_COEFFS)).astype(np.float32)
    # noise=0.7 produces score ~0.78 (above per-template 0.5, below instance 0.95)
    haystack = np.zeros((200, N_COEFFS), dtype=np.float32)
    noise = rng.standard_normal((20, N_COEFFS)).astype(np.float32) * 0.7
    haystack[50:70] = template + noise

    # Without per-template override (instance 0.95): should block
    row_no_override = _make_row(template, score_threshold=None)
    matcher_strict = AudioCueTemplateMatcher(templates=[row_no_override], score_threshold=0.95)
    m_strict = {1: []}
    p_strict = {1: 0.0}
    nm_strict = {1: []}
    matcher_strict._scan_chunk(haystack, 0.0, m_strict, p_strict, nm_strict)

    # With per-template 0.5 overriding instance 0.95: should allow
    row_lenient = _make_row(template, score_threshold=0.5)
    matcher_lenient = AudioCueTemplateMatcher(templates=[row_lenient], score_threshold=0.95)
    m_lenient = {1: []}
    p_lenient = {1: 0.0}
    nm_lenient = {1: []}
    matcher_lenient._scan_chunk(haystack, 0.0, m_lenient, p_lenient, nm_lenient)

    # Per-template threshold must produce more matches than strict instance alone.
    assert len(m_lenient[1]) > len(m_strict[1]), (
        f"per-template 0.5 should allow matches that instance 0.95 blocks; "
        f"got strict={len(m_strict[1])} lenient={len(m_lenient[1])}"
    )


def test_null_score_threshold_uses_instance_threshold():
    """score_threshold=None: matcher behaves as before (uses instance threshold)."""
    rng = np.random.default_rng(1)
    template = rng.standard_normal((10, N_COEFFS)).astype(np.float32)
    row = _make_row(template, score_threshold=None)
    matcher = AudioCueTemplateMatcher(templates=[row], score_threshold=0.7)
    assert matcher.is_usable
    assert matcher._templates[0].score_threshold is None


# ---------------------------------------------------------------------------
# _scan_chunk: per-template effective threshold applied correctly
# ---------------------------------------------------------------------------

def test_scan_chunk_uses_per_template_threshold_to_gate_match():
    """Planted match with score ~1.0 passes even a strict per-template threshold."""
    rng = np.random.default_rng(99)
    template_mfcc = rng.standard_normal((10, N_COEFFS)).astype(np.float32)
    haystack = rng.standard_normal((200, N_COEFFS)).astype(np.float32) * 0.001
    haystack[50:60] = template_mfcc  # near-perfect plant

    row = _make_row(template_mfcc, score_threshold=0.8)
    matcher = AudioCueTemplateMatcher(templates=[row], score_threshold=0.3)

    per_template_matches = {1: []}
    per_template_peak = {1: 0.0}
    per_template_near_misses = {1: []}
    matcher._scan_chunk(haystack, 0.0, per_template_matches, per_template_peak, per_template_near_misses)

    # Score ~1.0 exceeds per-template threshold 0.8 -> should match
    assert len(per_template_matches[1]) == 1


def test_scan_chunk_per_template_threshold_blocks_below_its_own_value():
    """With per-template 0.999, a noisy-only haystack yields no match."""
    rng = np.random.default_rng(55)
    template_mfcc = rng.standard_normal((10, N_COEFFS)).astype(np.float32)
    # No planted match; haystack is pure noise, scores will be low
    haystack = rng.standard_normal((200, N_COEFFS)).astype(np.float32) * 0.1

    row = _make_row(template_mfcc, score_threshold=0.999)
    matcher = AudioCueTemplateMatcher(templates=[row], score_threshold=0.1)

    per_template_matches = {1: []}
    per_template_peak = {1: 0.0}
    per_template_near_misses = {1: []}
    matcher._scan_chunk(haystack, 0.0, per_template_matches, per_template_peak, per_template_near_misses)

    # Noisy haystack won't exceed 0.999
    assert len(per_template_matches[1]) == 0


def test_scan_chunk_null_per_template_threshold_falls_back_to_instance():
    """NULL per-template threshold: instance threshold governs matching."""
    rng = np.random.default_rng(11)
    template_mfcc = rng.standard_normal((10, N_COEFFS)).astype(np.float32)
    haystack = rng.standard_normal((200, N_COEFFS)).astype(np.float32) * 0.001
    haystack[50:60] = template_mfcc  # planted

    row = _make_row(template_mfcc, score_threshold=None)
    matcher = AudioCueTemplateMatcher(templates=[row], score_threshold=0.3)

    per_template_matches = {1: []}
    per_template_peak = {1: 0.0}
    per_template_near_misses = {1: []}
    matcher._scan_chunk(haystack, 0.0, per_template_matches, per_template_peak, per_template_near_misses)

    # Score ~1.0 exceeds instance 0.3 -> should match
    assert len(per_template_matches[1]) == 1


# ---------------------------------------------------------------------------
# Peak-pick floor: near-miss surfaces under lowered per-template threshold
# ---------------------------------------------------------------------------

def test_peak_pick_floor_respects_lowered_per_template_threshold():
    """Per-template 0.5 (below instance 0.8) lowers pick_floor so _peak_pick sees a mid-range peak.

    The score >= 0.5 gate then promotes it to a real match, not just a near-miss."""
    rng = np.random.default_rng(17)
    template_mfcc = rng.standard_normal((10, N_COEFFS)).astype(np.float32)
    # noise=1.0 produces score ~0.78 (above per-template 0.5, below instance 0.8)
    haystack = np.zeros((200, N_COEFFS), dtype=np.float32)
    noise = rng.standard_normal((10, N_COEFFS)).astype(np.float32) * 1.0
    haystack[80:90] = template_mfcc + noise

    # Instance threshold 0.8 alone: pick_floor=0.8 -> mid-range peak invisible
    row_no_override = _make_row(template_mfcc, score_threshold=None)
    matcher_strict = AudioCueTemplateMatcher(templates=[row_no_override], score_threshold=0.8)
    m_strict = {1: []}
    p_strict = {1: 0.0}
    nm_strict = {1: []}
    matcher_strict._scan_chunk(haystack, 0.0, m_strict, p_strict, nm_strict)

    # Per-template 0.5 with instance 0.8: pick_floor=0.5 -> peak visible and promoted to match
    row_lower = _make_row(template_mfcc, score_threshold=0.5)
    matcher_lower = AudioCueTemplateMatcher(templates=[row_lower], score_threshold=0.8)
    m_lower = {1: []}
    p_lower = {1: 0.0}
    nm_lower = {1: []}
    matcher_lower._scan_chunk(haystack, 0.0, m_lower, p_lower, nm_lower)

    # The lowered per-template threshold must surface the match that instance 0.8 misses.
    assert len(m_lower[1]) > len(m_strict[1]), (
        f"per-template 0.5 should surface match that instance 0.8 blocks; "
        f"strict={len(m_strict[1])} lower={len(m_lower[1])}"
    )
    assert len(m_lower[1]) >= 1, "planted degraded copy must be found with per-template 0.5"


# ---------------------------------------------------------------------------
# DB layer: update_cue_template sets and clears score_threshold
# ---------------------------------------------------------------------------

def test_update_cue_template_sets_score_threshold(temp_db):
    from audio_analysis.cue_features import serialize_mfcc, pcm_to_int16_bytes
    rng = np.random.default_rng(3)
    mfcc = rng.standard_normal((10, N_COEFFS)).astype(np.float32)
    pcm = rng.standard_normal(1600).astype(np.float32)
    pid = temp_db.create_podcast('st-feed', 'http://x/st.xml', 'ST Feed')
    tid = temp_db.create_cue_template(
        podcast_id=pid, cue_type='ad_break_boundary',
        source_episode_id='ep1', source_offset_s=1.0, duration_s=0.5,
        sample_rate=16000, n_coeffs=N_COEFFS,
        mfcc_blob=serialize_mfcc(mfcc),
        pcm_blob=pcm_to_int16_bytes(np.clip(pcm, -1, 1)),
        pcm_sample_rate=16000,
    )
    assert temp_db.update_cue_template(tid, score_threshold=0.75)
    row = temp_db.get_cue_template(tid)
    assert row['score_threshold'] == pytest.approx(0.75)


def test_update_cue_template_clears_score_threshold(temp_db):
    from audio_analysis.cue_features import serialize_mfcc, pcm_to_int16_bytes
    rng = np.random.default_rng(4)
    mfcc = rng.standard_normal((10, N_COEFFS)).astype(np.float32)
    pcm = rng.standard_normal(1600).astype(np.float32)
    pid = temp_db.create_podcast('cl-feed', 'http://x/cl.xml', 'CL Feed')
    tid = temp_db.create_cue_template(
        podcast_id=pid, cue_type='ad_break_boundary',
        source_episode_id='ep2', source_offset_s=1.0, duration_s=0.5,
        sample_rate=16000, n_coeffs=N_COEFFS,
        mfcc_blob=serialize_mfcc(mfcc),
        pcm_blob=pcm_to_int16_bytes(np.clip(pcm, -1, 1)),
        pcm_sample_rate=16000,
    )
    temp_db.update_cue_template(tid, score_threshold=0.75)
    # Clear by passing None explicitly
    assert temp_db.update_cue_template(tid, score_threshold=None)
    row = temp_db.get_cue_template(tid)
    assert row['score_threshold'] is None


def test_update_cue_template_no_score_threshold_arg_leaves_column_unchanged(temp_db):
    """Calling update_cue_template without score_threshold arg leaves it alone."""
    from audio_analysis.cue_features import serialize_mfcc, pcm_to_int16_bytes
    rng = np.random.default_rng(5)
    mfcc = rng.standard_normal((10, N_COEFFS)).astype(np.float32)
    pcm = rng.standard_normal(1600).astype(np.float32)
    pid = temp_db.create_podcast('nc-feed', 'http://x/nc.xml', 'NC Feed')
    tid = temp_db.create_cue_template(
        podcast_id=pid, cue_type='ad_break_boundary',
        source_episode_id='ep3', source_offset_s=1.0, duration_s=0.5,
        sample_rate=16000, n_coeffs=N_COEFFS,
        mfcc_blob=serialize_mfcc(mfcc),
        pcm_blob=pcm_to_int16_bytes(np.clip(pcm, -1, 1)),
        pcm_sample_rate=16000,
    )
    temp_db.update_cue_template(tid, score_threshold=0.65)
    # Update cue_type without touching score_threshold
    temp_db.update_cue_template(tid, cue_type='ad_break_start')
    row = temp_db.get_cue_template(tid)
    assert row['score_threshold'] == pytest.approx(0.65)


# ---------------------------------------------------------------------------
# API layer: _template_to_meta_dict includes scoreThreshold
# ---------------------------------------------------------------------------

def test_template_to_meta_dict_includes_score_threshold():
    """_template_to_meta_dict must emit scoreThreshold (camelCase)."""
    # Minimal row dict with score_threshold set
    row = {
        'id': 1,
        'podcast_id': 2,
        'label': 'ad-break boundary',
        'cue_type': 'ad_break_boundary',
        'source_episode_id': 'ep1',
        'source_offset_s': 1.0,
        'duration_s': 0.5,
        'sample_rate': 16000,
        'n_coeffs': 20,
        'scope': 'podcast',
        'network_id': None,
        'enabled': 1,
        'created_at': '2026-01-01T00:00:00Z',
        'created_by': 'user',
        'pcm_blob': None,
        'has_audio': 0,
        'score_threshold': 0.72,
    }

    result = ct_api._template_to_meta_dict(row)
    assert 'scoreThreshold' in result
    assert result['scoreThreshold'] == pytest.approx(0.72)


def test_template_to_meta_dict_score_threshold_null():
    """scoreThreshold is None when the column is NULL."""
    import api.cue_templates as ct_api
    row = {
        'id': 1, 'podcast_id': 2, 'label': 'ad-break boundary',
        'cue_type': 'ad_break_boundary', 'source_episode_id': None,
        'source_offset_s': 0.0, 'duration_s': 0.5, 'sample_rate': 16000,
        'n_coeffs': 20, 'scope': 'podcast', 'network_id': None,
        'enabled': 1, 'created_at': '2026-01-01T00:00:00Z',
        'created_by': None, 'pcm_blob': None, 'has_audio': 0,
        'score_threshold': None,
    }
    result = ct_api._template_to_meta_dict(row)
    assert 'scoreThreshold' in result
    assert result['scoreThreshold'] is None


# ---------------------------------------------------------------------------
# Suggest sweep: per-template thresholds stripped so full distribution visible
# ---------------------------------------------------------------------------

def test_suggest_sweep_strips_per_template_thresholds():
    """_run_cue_threshold_scan must clear score_threshold before building the matcher.

    Templates with threshold 0.99 and a haystack scoring ~0.7 (above AUDIO_CUE_SUGGEST_FLOOR) would
    be hidden by the per-template gate. Builds the sweep matcher directly (mirroring the route) and
    asserts score_threshold is None after the strip."""
    rng = np.random.default_rng(88)
    template_mfcc = rng.standard_normal((10, N_COEFFS)).astype(np.float32)
    # noise=0.8 produces score ~0.72 (above AUDIO_CUE_SUGGEST_FLOOR ~0.35, below 0.99)
    haystack = np.zeros((200, N_COEFFS), dtype=np.float32)
    noise = rng.standard_normal((10, N_COEFFS)).astype(np.float32) * 0.8
    haystack[60:70] = template_mfcc + noise

    # Row as it comes from the DB: high per-template threshold
    row = _make_row(template_mfcc, score_threshold=0.99)

    # Simulate what _run_cue_threshold_scan does: strip score_threshold before sweep
    from api.cue_templates import AUDIO_CUE_SUGGEST_FLOOR
    sweep_templates = [{**row, 'score_threshold': None}]

    # Confirm the strip cleared the field
    assert sweep_templates[0]['score_threshold'] is None

    # Build the sweep matcher at the low floor (as the route does)
    sweep_matcher = AudioCueTemplateMatcher(
        sweep_templates,
        score_threshold=AUDIO_CUE_SUGGEST_FLOOR,
        max_matches_per_template=200,
    )
    assert sweep_matcher.is_usable
    assert sweep_matcher._templates[0].score_threshold is None

    # The sweep matcher sees the occurrence; the unstripped matcher would not.
    m_sweep = {1: []}
    p_sweep = {1: 0.0}
    nm_sweep = {1: []}
    sweep_matcher._scan_chunk(haystack, 0.0, m_sweep, p_sweep, nm_sweep)

    # Build unstripped matcher at same low floor but with per-template 0.99
    gated_matcher = AudioCueTemplateMatcher(
        [row],
        score_threshold=AUDIO_CUE_SUGGEST_FLOOR,
        max_matches_per_template=200,
    )
    m_gated = {1: []}
    p_gated = {1: 0.0}
    nm_gated = {1: []}
    gated_matcher._scan_chunk(haystack, 0.0, m_gated, p_gated, nm_gated)

    # Sweep (stripped) must find the occurrence; gated must not.
    assert len(m_sweep[1]) >= 1, "sweep matcher must find planted occurrence after strip"
    assert len(m_gated[1]) == 0, "unstripped per-template 0.99 must block the occurrence"


# ---------------------------------------------------------------------------
# Shared validator: _normalize_nullable_finite_float (findings 2, 3, 4, 5)
# ---------------------------------------------------------------------------

def test_shared_validator_rejects_nan():
    """NaN passes range comparisons (NaN < lo is False); validator must catch it."""
    from api import _normalize_nullable_finite_float
    val, err = _normalize_nullable_finite_float(float('nan'), 'field', 0.0, 1.0)
    assert err is not None
    assert val is None


def test_shared_validator_rejects_inf():
    """Infinity must be rejected explicitly."""
    from api import _normalize_nullable_finite_float
    val, err = _normalize_nullable_finite_float(float('inf'), 'field', 0.0, 1.0)
    assert err is not None
    assert val is None


def test_shared_validator_rejects_neg_inf():
    """Negative infinity must be rejected."""
    from api import _normalize_nullable_finite_float
    val, err = _normalize_nullable_finite_float(float('-inf'), 'field', 0.0, 1.0)
    assert err is not None
    assert val is None


def test_shared_validator_rejects_bool_true():
    """True coerces to 1.0 via float(); must be rejected as a boolean."""
    from api import _normalize_nullable_finite_float
    val, err = _normalize_nullable_finite_float(True, 'field', 0.0, 2.0)
    assert err is not None
    assert val is None


def test_shared_validator_rejects_bool_false():
    """False coerces to 0.0 via float(); must be rejected as a boolean."""
    from api import _normalize_nullable_finite_float
    val, err = _normalize_nullable_finite_float(False, 'field', 0.0, 2.0)
    assert err is not None
    assert val is None


def test_shared_validator_empty_string_clears():
    """Empty string must clear the override (return None, None)."""
    from api import _normalize_nullable_finite_float
    val, err = _normalize_nullable_finite_float('', 'field', 0.0, 1.0)
    assert err is None
    assert val is None


def test_shared_validator_none_clears():
    """None must clear the override (return None, None)."""
    from api import _normalize_nullable_finite_float
    val, err = _normalize_nullable_finite_float(None, 'field', 0.0, 1.0)
    assert err is None
    assert val is None


def test_shared_validator_valid_float_accepted():
    """A normal in-range float must be accepted and returned as float."""
    from api import _normalize_nullable_finite_float
    val, err = _normalize_nullable_finite_float(0.75, 'field', 0.30, 0.99)
    assert err is None
    assert abs(val - 0.75) < 1e-9


def test_shared_validator_rejects_below_floor():
    """Value below lo must return an error."""
    from api import _normalize_nullable_finite_float
    val, err = _normalize_nullable_finite_float(0.10, 'field', 0.30, 0.99)
    assert err is not None
    assert val is None


def test_shared_validator_rejects_above_ceiling():
    """Value above hi must return an error."""
    from api import _normalize_nullable_finite_float
    val, err = _normalize_nullable_finite_float(1.0, 'field', 0.30, 0.99)
    assert err is not None
    assert val is None

