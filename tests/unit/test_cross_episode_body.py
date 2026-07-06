"""Tests for discover_cross_episode_body: full-body recurring-segment discovery.

_find_shared_segments is body-agnostic; discover_cross_episode_cues restricts it
to head/tail windows. discover_cross_episode_body passes the full fingerprint
arrays so mid-episode stings are found alongside head/tail ones.

Fixture style mirrors test_cross_episode_cues.py: synthetic uint32 arrays with
planted segments, no fpcalc required.
"""
from unittest.mock import patch

import numpy as np

from audio_fingerprinter import AudioFingerprinter

WIN = 16   # ~2s at 8 fps -- matches AUDIO_CUE_FP_WINDOW_SECONDS default


def _rand(n, rng):
    """Random uint32 fingerprint ints that will not accidentally match at 0.73."""
    return rng.integers(0, 2 ** 32, size=n, dtype=np.uint64).astype(np.uint32)


def _make_fp(n=300, rng=None):
    """Return (int_list, duration) for an n-int fingerprint at 8 fps."""
    r = rng or np.random.default_rng(99)
    return _rand(n, r).tolist(), n / 8.0


def _fingerprinter():
    fp = AudioFingerprinter.__new__(AudioFingerprinter)
    fp.db = None
    fp._fpcalc_path = '/fake/fpcalc'
    return fp


# ---------------------------------------------------------------------------
# Core planted-mid-body-segment test (TDD anchor)
# ---------------------------------------------------------------------------

def test_finds_mid_body_segment_in_two_siblings():
    # Segment planted near the middle of a 300-int episode.
    rng = np.random.default_rng(42)
    seg = _rand(40, rng)        # ~5s shared sting

    # Target: noise | seg | noise (seg starts at index 130, well past any head window)
    t_noise_a = _rand(130, rng)
    t_noise_b = _rand(130, rng)
    t_ints = np.concatenate([t_noise_a, seg, t_noise_b])
    t_dur = len(t_ints) / 8.0

    # Two siblings share the same segment at different offsets
    s1_ints = np.concatenate([_rand(80, rng), seg, _rand(100, rng)])
    s2_ints = np.concatenate([_rand(150, rng), seg, _rand(60, rng)])

    fp = _fingerprinter()
    side_effects = [
        (t_ints.tolist(), t_dur),
        (s1_ints.tolist(), len(s1_ints) / 8.0),
        (s2_ints.tolist(), len(s2_ints) / 8.0),
    ]
    with patch.object(fp, '_generate_full_fingerprint', side_effect=side_effects):
        result = fp.discover_cross_episode_body(
            'target.mp3', ['sib1.mp3', 'sib2.mp3'],
            min_matches=2,
        )

    assert len(result) >= 1
    r = result[0]
    assert r['kind'] == 'recurring'
    assert 'episodeMatches' in r
    assert r['episodeMatches'] >= 2
    # Segment planted at index 130 -> 130/8 = 16.25s; allow WIN/2 tolerance
    assert abs(r['start'] - 130 / 8.0) <= WIN / 8.0 + 0.5
    assert 'start' in r and 'end' in r
    assert r['end'] > r['start']


# ---------------------------------------------------------------------------
# Two-episode set (one sibling, min_matches=1) returns a candidate. Regression
# guard for finding 1: the worker caps min_matches at the sibling count, so a
# 2-episode scan reaches the real function with min_matches=1 and is not empty.
# ---------------------------------------------------------------------------

def test_two_episode_set_single_sibling_returns_candidate():
    rng = np.random.default_rng(41)
    seg = _rand(40, rng)  # ~5s shared sting

    t_ints = np.concatenate([_rand(130, rng), seg, _rand(130, rng)])
    t_dur = len(t_ints) / 8.0
    s1_ints = np.concatenate([_rand(80, rng), seg, _rand(100, rng)])

    fp = _fingerprinter()
    side_effects = [
        (t_ints.tolist(), t_dur),
        (s1_ints.tolist(), len(s1_ints) / 8.0),
    ]
    with patch.object(fp, '_generate_full_fingerprint', side_effect=side_effects):
        result = fp.discover_cross_episode_body(
            'target.mp3', ['sib1.mp3'],
            min_matches=1,
        )

    assert len(result) >= 1
    assert result[0]['kind'] == 'recurring'
    assert result[0]['episodeMatches'] >= 1


# ---------------------------------------------------------------------------
# Finding 2: a recurring 2.0-2.4s sting is returned by the REAL function using
# the default min_duration (AUDIO_CUE_XEP_BODY_MIN_DURATION=2.0), not the 3s
# intro/outro floor that used to discard sub-3s stings.
# ---------------------------------------------------------------------------

def test_short_recurring_sting_survives_default_min_duration():
    rng = np.random.default_rng(53)
    seg = _rand(19, rng)  # 19 ints / 8 fps = 2.375s, in the 2.0-2.4s band

    t_ints = np.concatenate([_rand(120, rng), seg, _rand(120, rng)])
    t_dur = len(t_ints) / 8.0
    s1_ints = np.concatenate([_rand(70, rng), seg, _rand(70, rng)])
    s2_ints = np.concatenate([_rand(90, rng), seg, _rand(90, rng)])

    fp = _fingerprinter()
    side_effects = [
        (t_ints.tolist(), t_dur),
        (s1_ints.tolist(), len(s1_ints) / 8.0),
        (s2_ints.tolist(), len(s2_ints) / 8.0),
    ]
    # No min_duration override: exercise the body-scan default.
    with patch.object(fp, '_generate_full_fingerprint', side_effect=side_effects):
        result = fp.discover_cross_episode_body(
            'target.mp3', ['sib1.mp3', 'sib2.mp3'],
            min_matches=2,
        )

    assert len(result) >= 1
    r = result[0]
    assert r['kind'] == 'recurring'
    assert (r['end'] - r['start']) < 3.0  # below the old 3s floor


# ---------------------------------------------------------------------------
# min_matches gate: only one sibling shares -> not returned
# ---------------------------------------------------------------------------

def test_not_returned_when_fewer_siblings_than_min_matches():
    rng = np.random.default_rng(7)
    seg = _rand(40, rng)

    t_ints = np.concatenate([_rand(120, rng), seg, _rand(120, rng)])
    t_dur = len(t_ints) / 8.0
    s1_ints = np.concatenate([_rand(80, rng), seg, _rand(80, rng)])  # shares seg
    s2_ints = _rand(len(t_ints), rng)                                 # no match

    fp = _fingerprinter()
    side_effects = [
        (t_ints.tolist(), t_dur),
        (s1_ints.tolist(), len(s1_ints) / 8.0),
        (s2_ints.tolist(), len(s2_ints) / 8.0),
    ]
    with patch.object(fp, '_generate_full_fingerprint', side_effect=side_effects):
        result = fp.discover_cross_episode_body(
            'target.mp3', ['sib1.mp3', 'sib2.mp3'],
            min_matches=2,
        )

    assert result == []


# ---------------------------------------------------------------------------
# Head AND mid-body: both found (no window restriction)
# ---------------------------------------------------------------------------

def test_finds_both_head_and_mid_body_segments():
    rng = np.random.default_rng(11)
    seg_head = _rand(32, rng)   # near start of episode
    seg_mid = _rand(32, rng)    # mid-body

    # Target layout: seg_head | noise | seg_mid | noise
    t_ints = np.concatenate([
        seg_head,
        _rand(100, rng),
        seg_mid,
        _rand(100, rng),
    ])
    t_dur = len(t_ints) / 8.0

    def _make_sib(rng_sib):
        return np.concatenate([
            _rand(10, rng_sib), seg_head, _rand(80, rng_sib),
            _rand(10, rng_sib), seg_mid, _rand(50, rng_sib),
        ])

    rng2, rng3 = np.random.default_rng(111), np.random.default_rng(222)
    s1 = _make_sib(rng2)
    s2 = _make_sib(rng3)

    fp = _fingerprinter()
    side_effects = [
        (t_ints.tolist(), t_dur),
        (s1.tolist(), len(s1) / 8.0),
        (s2.tolist(), len(s2) / 8.0),
    ]
    with patch.object(fp, '_generate_full_fingerprint', side_effect=side_effects):
        result = fp.discover_cross_episode_body(
            'target.mp3', ['sib1.mp3', 'sib2.mp3'],
            min_matches=2,
        )

    assert len(result) >= 2
    for r in result:
        assert r['kind'] == 'recurring'
        assert r['episodeMatches'] >= 2


# ---------------------------------------------------------------------------
# max_len_s cap respected
# ---------------------------------------------------------------------------

def test_max_len_cap_respected():
    rng = np.random.default_rng(13)
    seg = _rand(200, rng)   # much longer than cap

    t_ints = np.concatenate([_rand(50, rng), seg, _rand(50, rng)])
    t_dur = len(t_ints) / 8.0
    s1_ints = np.concatenate([_rand(20, rng), seg, _rand(20, rng)])
    s2_ints = np.concatenate([_rand(30, rng), seg, _rand(30, rng)])

    fp = _fingerprinter()
    side_effects = [
        (t_ints.tolist(), t_dur),
        (s1_ints.tolist(), len(s1_ints) / 8.0),
        (s2_ints.tolist(), len(s2_ints) / 8.0),
    ]
    max_s = 5.0
    with patch.object(fp, '_generate_full_fingerprint', side_effect=side_effects):
        result = fp.discover_cross_episode_body(
            'target.mp3', ['sib1.mp3', 'sib2.mp3'],
            min_matches=2, max_len_s=max_s,
        )

    assert len(result) >= 1
    for r in result:
        assert (r['end'] - r['start']) <= max_s + 0.5   # +0.5s rounding tolerance


# ---------------------------------------------------------------------------
# Empty siblings -> []
# ---------------------------------------------------------------------------

def test_empty_siblings_returns_empty():
    rng = np.random.default_rng(17)
    t_ints = _rand(200, rng)
    t_dur = len(t_ints) / 8.0

    fp = _fingerprinter()
    with patch.object(fp, '_generate_full_fingerprint', return_value=(t_ints.tolist(), t_dur)):
        result = fp.discover_cross_episode_body('target.mp3', [])

    assert result == []


# ---------------------------------------------------------------------------
# Target fingerprint reuse (target_fingerprint= kwarg)
# ---------------------------------------------------------------------------

def test_accepts_precomputed_target_fingerprint():
    rng = np.random.default_rng(19)
    seg = _rand(40, rng)
    t_ints = np.concatenate([_rand(100, rng), seg, _rand(100, rng)])
    t_dur = len(t_ints) / 8.0
    s1_ints = np.concatenate([_rand(60, rng), seg, _rand(60, rng)])
    s2_ints = np.concatenate([_rand(80, rng), seg, _rand(80, rng)])

    fp = _fingerprinter()
    # Only two calls -- for the two siblings -- not the target (precomputed)
    side_effects = [
        (s1_ints.tolist(), len(s1_ints) / 8.0),
        (s2_ints.tolist(), len(s2_ints) / 8.0),
    ]
    with patch.object(fp, '_generate_full_fingerprint', side_effect=side_effects) as mock_fp:
        result = fp.discover_cross_episode_body(
            'target.mp3', ['sib1.mp3', 'sib2.mp3'],
            min_matches=2,
            target_fingerprint=(t_ints.tolist(), t_dur),
        )

    # target_fingerprint was precomputed; _generate_full_fingerprint must NOT
    # have been called for the target (only twice total, for siblings).
    assert mock_fp.call_count == 2
    assert len(result) >= 1


# ---------------------------------------------------------------------------
# Result shape parity with discover_cross_episode_cues
# ---------------------------------------------------------------------------

def test_result_shape_matches_cross_episode_cues():
    rng = np.random.default_rng(23)
    seg = _rand(40, rng)
    t_ints = np.concatenate([_rand(100, rng), seg, _rand(100, rng)])
    t_dur = len(t_ints) / 8.0
    s1_ints = np.concatenate([_rand(60, rng), seg, _rand(60, rng)])
    s2_ints = np.concatenate([_rand(80, rng), seg, _rand(80, rng)])

    fp = _fingerprinter()
    side_effects = [
        (t_ints.tolist(), t_dur),
        (s1_ints.tolist(), len(s1_ints) / 8.0),
        (s2_ints.tolist(), len(s2_ints) / 8.0),
    ]
    with patch.object(fp, '_generate_full_fingerprint', side_effect=side_effects):
        result = fp.discover_cross_episode_body(
            'target.mp3', ['sib1.mp3', 'sib2.mp3'],
            min_matches=2,
        )

    assert len(result) >= 1
    for r in result:
        assert set(r.keys()) >= {'start', 'end', 'kind', 'episodeMatches'}
        assert isinstance(r['start'], float)
        assert isinstance(r['end'], float)
        assert r['kind'] == 'recurring'
        assert isinstance(r['episodeMatches'], int)


# ---------------------------------------------------------------------------
# fpcalc unavailable -> []
# ---------------------------------------------------------------------------

def test_returns_empty_when_fpcalc_unavailable():
    fp = _fingerprinter()
    fp._fpcalc_path = None
    result = fp.discover_cross_episode_body('target.mp3', ['sib1.mp3', 'sib2.mp3'])
    assert result == []


# ---------------------------------------------------------------------------
# Overlapping candidates from different sibling-pair passes dedupe correctly.
# Since all siblings are passed in a single _find_shared_segments call, the
# claimed_until mechanism handles this natively. Verify: one long segment
# shared across three siblings surfaces as ONE candidate, not multiple.
# ---------------------------------------------------------------------------

def test_overlapping_candidates_from_multiple_siblings_dedupe():
    rng = np.random.default_rng(31)
    seg = _rand(48, rng)

    t_ints = np.concatenate([_rand(100, rng), seg, _rand(100, rng)])
    t_dur = len(t_ints) / 8.0
    s1_ints = np.concatenate([_rand(50, rng), seg, _rand(50, rng)])
    s2_ints = np.concatenate([_rand(70, rng), seg, _rand(70, rng)])
    s3_ints = np.concatenate([_rand(30, rng), seg, _rand(30, rng)])

    fp = _fingerprinter()
    side_effects = [
        (t_ints.tolist(), t_dur),
        (s1_ints.tolist(), len(s1_ints) / 8.0),
        (s2_ints.tolist(), len(s2_ints) / 8.0),
        (s3_ints.tolist(), len(s3_ints) / 8.0),
    ]
    with patch.object(fp, '_generate_full_fingerprint', side_effect=side_effects):
        result = fp.discover_cross_episode_body(
            'target.mp3', ['sib1.mp3', 'sib2.mp3', 'sib3.mp3'],
            min_matches=2,
        )

    # The single planted segment must surface as exactly one candidate,
    # even though three siblings all share it.
    assert len(result) == 1
    assert result[0]['episodeMatches'] >= 2


def test_near_duplicate_matches_at_shifted_offsets_do_not_overlap():
    """Two siblings matching the target at OFFSET positions (8 frames apart)
    must not emit overlapping candidates: claimed_until operates on target
    frames regardless of which sibling produced the match."""
    rng = np.random.default_rng(77)
    seg = _rand(48, rng)

    # Target: seg occupies frames 130..178.
    t_ints = np.concatenate([_rand(130, rng), seg, _rand(120, rng)])
    t_dur = len(t_ints) / 8.0
    # Sibling 1: exact copy -> matches target frames 130..178.
    s1_ints = np.concatenate([_rand(60, rng), seg, _rand(60, rng)])
    # Sibling 2: copy of target[138:186] (seg shifted by 8 frames plus 8
    # frames of trailing target noise) -> matches target frames 138..186.
    s2_ints = np.concatenate([_rand(60, rng), t_ints[138:186], _rand(60, rng)])

    fp = _fingerprinter()
    side_effects = [
        (t_ints.tolist(), t_dur),
        (s1_ints.tolist(), len(s1_ints) / 8.0),
        (s2_ints.tolist(), len(s2_ints) / 8.0),
    ]
    with patch.object(fp, '_generate_full_fingerprint', side_effect=side_effects):
        result = fp.discover_cross_episode_body(
            'target.mp3', ['sib1.mp3', 'sib2.mp3'],
            min_matches=1,
        )

    # The planted region collapses to one candidate; nothing overlaps.
    assert len(result) == 1
    spans = sorted((r['start'], r['end']) for r in result)
    for (a_start, a_end), (b_start, b_end) in zip(spans, spans[1:]):
        assert b_start >= a_end, f"overlapping candidates: {spans}"
    # Candidate lands on the planted region (frames 130..186 -> 16.25s..23.25s).
    assert 15.0 <= result[0]['start'] <= 18.0


# ---------------------------------------------------------------------------
# Existing discover_cross_episode_cues tests must remain green.
# (Verified by running the full suite; this is a cross-check import guard.)
# ---------------------------------------------------------------------------

def test_import_guard_existing_function_still_importable():
    from audio_fingerprinter import AudioFingerprinter as AF
    assert hasattr(AF, 'discover_cross_episode_cues')
    assert hasattr(AF, 'discover_cross_episode_body')
