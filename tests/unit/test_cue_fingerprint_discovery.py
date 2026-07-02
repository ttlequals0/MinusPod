"""Unit tests for fingerprint self-repeat cue discovery (#350).

Exercises the pure functions over a synthetic fpcalc ``-raw`` int array: a
distinctive 16-int "sound" planted at several well-separated positions, with the
rest filled by a unique-per-index sequence that never repeats. No fpcalc needed.
"""
from unittest.mock import patch

import numpy as np

from audio_fingerprinter import AudioFingerprinter, _discover_repeats, _count_window_matches

FPS = 8.0            # fpcalc -raw emits ~8 ints/sec
FP_DURATION = 100.0  # so n = 800 ints
N = int(FPS * FP_DURATION)
WIN = 16             # AUDIO_CUE_FP_WINDOW_SECONDS (2.0) * FPS


def _noise(i):
    # Knuth multiplicative hash: distinct, well-spread, never-repeating per index.
    return (i * 2654435761) & 0xFFFFFFFF


def _planted_array(positions):
    """An N-int array with a fixed 16-int pattern planted at each position."""
    arr = [_noise(i) for i in range(N)]
    pattern = [(0xA5A5A5A5 ^ (k * 0x01010101)) & 0xFFFFFFFF for k in range(WIN)]
    for p in positions:
        arr[p:p + WIN] = pattern
    return arr


def test_discovers_planted_repeat():
    # Pattern at 80, 320, 560 (>=30s apart, well over the 5s min-gap).
    arr = _planted_array([80, 320, 560])
    cands = _discover_repeats(arr, FP_DURATION, similarity=0.75, min_count=3)
    assert len(cands) == 1
    c = cands[0]
    assert c['count'] == 3
    # ~10.0s; backward extension may walk up to a probe step into the lead-in.
    assert abs(c['start'] - 80 / FPS) <= 1.0


def test_below_min_count_is_dropped():
    # Only two occurrences: below the default min_count of 3.
    arr = _planted_array([80, 320])
    assert _discover_repeats(arr, FP_DURATION, similarity=0.75, min_count=3) == []


def test_unique_noise_yields_no_candidates():
    arr = [_noise(i) for i in range(N)]
    assert _discover_repeats(arr, FP_DURATION, similarity=0.75, min_count=3) == []


def test_empty_or_degenerate_input():
    assert _discover_repeats([], FP_DURATION, similarity=0.75, min_count=3) == []
    assert _discover_repeats([1, 2, 3], 0.0, similarity=0.75, min_count=3) == []


def test_count_self_matches_recurring_window():
    arr = _planted_array([80, 320, 560])
    # The planted window [10s, 12s] recurs three times.
    assert _count_window_matches(arr, FP_DURATION, 10.0, 12.0, similarity=0.75) == 3


def test_count_self_matches_one_off_window():
    arr = _planted_array([80])
    # Window over a non-recurring noise stretch appears only once.
    assert _count_window_matches(arr, FP_DURATION, 50.0, 52.0, similarity=0.75) == 1


def test_per_zone_caps_intro_max():
    # discover_cross_episode_cues passes intro_max_duration and
    # outro_max_duration as separate per-zone caps; a long intro is capped at
    # intro_max_duration but not at outro_max_duration.
    fp = AudioFingerprinter.__new__(AudioFingerprinter)
    fp.db = None
    fp._fpcalc_path = '/fake/fpcalc'

    rng = np.random.default_rng(10)
    shared = rng.integers(0, 2 ** 32, 48, dtype=np.uint64).astype(np.uint32)
    t_ints = np.concatenate([shared, rng.integers(0, 2 ** 32, 72, dtype=np.uint64).astype(np.uint32)])
    s_ints = np.concatenate([shared, rng.integers(0, 2 ** 32, 72, dtype=np.uint64).astype(np.uint32)])
    t_dur = 15.0  # 120 ints / 8fps = 15s

    with patch.object(fp, '_generate_full_fingerprint',
                      side_effect=[(t_ints.tolist(), t_dur), (s_ints.tolist(), t_dur)]):
        result = fp.discover_cross_episode_cues(
            'fake_target.mp3', ['fake_sib.mp3'],
            head_seconds=10.0, tail_seconds=5.0,
            window_seconds=2.0, similarity=0.73,
            min_matches=1,
            min_duration=1.0,
            intro_max_duration=4.0,
            outro_max_duration=60.0,
            max_per_zone=3,
        )

    intros = [r for r in result if r['kind'] == 'intro']
    assert len(intros) >= 1  # fixture must produce at least one intro match
    for intro in intros:
        assert (intro['end'] - intro['start']) <= 4.5  # 4.0s cap + half window tolerance


def test_discover_repeats_includes_occurrences():
    """Each candidate must carry onset-aligned occurrences for ad-affinity typing."""
    arr = _planted_array([80, 320, 560])
    candidates = _discover_repeats(arr, FP_DURATION, similarity=0.75, min_count=3)
    assert candidates, 'expected at least one candidate'
    for c in candidates:
        assert 'occurrences' in c
        assert isinstance(c['occurrences'], list)
        assert len(c['occurrences']) >= 2
