"""Measured fingerprint matcher: inverted-index candidates, run measurement, gap bridging."""
import random
import time

import numpy as np

import audio_fingerprinter as af
from audio_fingerprinter import match_fingerprints

IPS = 8.0
KW = dict(strictness=0.65, min_coverage=0.9, gap_tolerance_s=3.5)


def _rand_ints(rng, n):
    return [rng.getrandbits(32) - (1 << 31) for _ in range(n)]


def _plant(episode, pattern, idx, rng, flips_per_int):
    for j, v in enumerate(pattern):
        noisy = v
        for _ in range(flips_per_int):
            noisy ^= 1 << rng.randrange(32)
        episode[idx + j] = noisy


def _episode(rng, seconds):
    ep = _rand_ints(rng, int(seconds * IPS))
    return ep, len(ep) / IPS


def _run(ep, dur, patterns, **over):
    return match_fingerprints(ep, dur, patterns, **{**KW, **over})


def test_offstep_start_is_measured():
    rng = random.Random(1)
    ep, dur = _episode(rng, 120)
    pat = _rand_ints(rng, 80)
    idx = int(37.3 * IPS)
    _plant(ep, pat, idx, rng, 2)
    r = _run(ep, dur, [(1, pat, 10.0, None, None)])
    assert len(r) == 1
    assert abs(r[0].start - idx / IPS) < 0.3
    assert r[0].coverage >= 0.95 and r[0].similarity >= 0.9
    assert r[0].confidence == r[0].similarity


def test_truncated_pattern_reports_low_coverage():
    rng = random.Random(2)
    ep, dur = _episode(rng, 60)
    pat = _rand_ints(rng, 160)
    _plant(ep, pat[:80], len(ep) - 80, rng, 1)
    assert _run(ep, dur, [(1, pat, 20.0, None, None)]) == []
    r = _run(ep, dur, [(1, pat, 20.0, None, None)], min_coverage=0.4)
    assert 0.45 <= r[0].coverage <= 0.6


def _gapped(gap_s, seed):
    rng = random.Random(seed)
    ep, dur = _episode(rng, 120)
    pat = _rand_ints(rng, 160)
    _plant(ep, pat, 400, rng, 1)
    mid = 400 + 80
    for j in range(int(gap_s * IPS)):
        ep[mid + j] = rng.getrandbits(32) - (1 << 31)
    return ep, dur, pat


def test_gap_within_tolerance_is_one_run():
    ep, dur, pat = _gapped(2.0, 3)
    r = _run(ep, dur, [(1, pat, 20.0, None, None)], min_coverage=0.5)
    assert len(r) == 1
    assert r[0].coverage >= 0.95
    assert abs(r[0].start - 50.0) < 0.5 and abs(r[0].end - 70.0) < 0.5


def test_gap_beyond_tolerance_truncates_run():
    ep, dur, pat = _gapped(6.0, 4)
    r = _run(ep, dur, [(1, pat, 20.0, None, None)], min_coverage=0.3)
    assert r and all(m.coverage < 0.8 for m in r)
    assert all(m.end - m.start < 14.0 for m in r)


def test_two_patterns_back_to_back():
    rng = random.Random(5)
    ep, dur = _episode(rng, 120)
    a, b = _rand_ints(rng, 80), _rand_ints(rng, 96)
    _plant(ep, a, 300, rng, 1)
    _plant(ep, b, 380, rng, 1)
    r = _run(ep, dur, [(2, b, 12.0, None, None), (1, a, 10.0, None, None)])
    assert [m.pattern_id for m in r] == [1, 2]
    assert r[0].end <= r[1].start


def test_pattern_longer_than_remaining_audio():
    rng = random.Random(6)
    ep, dur = _episode(rng, 60)
    pat = _rand_ints(rng, 240)
    _plant(ep, pat[:80], len(ep) - 80, rng, 1)
    r = _run(ep, dur, [(1, pat, 30.0, None, None)], min_coverage=0.3)
    assert 0.28 <= r[0].coverage <= 0.38


def test_corrupt_pattern_is_skipped_not_fatal(caplog):
    rng = random.Random(7)
    ep, dur = _episode(rng, 60)
    pat = _rand_ints(rng, 80)
    _plant(ep, pat, 200, rng, 1)
    bad = [(2, [], 5.0, None, None), (3, [1, 2], 5.0, None, None)]
    with caplog.at_level('DEBUG', logger='podcast.fingerprint'):
        r = _run(ep, dur, bad + [(1, pat, 10.0, None, None)], min_coverage=0.5)
    assert [m.pattern_id for m in r] == [1]
    skipped = [x for x in caplog.records if x.levelname == 'DEBUG' and 'Skipping' in x.getMessage()]
    assert len(skipped) == 2


def test_constant_stretches_do_not_go_quadratic():
    rng = random.Random(8)
    ep, dur = _episode(rng, 2400)
    const = 123456789
    ep[100:100 + 4800] = [const] * 4800
    planted = _rand_ints(rng, 160)
    _plant(ep, planted, 12000, rng, 1)
    pats = []
    for pid in range(1, 41):
        body = _rand_ints(rng, 480)
        body[40:440] = [const] * 400
        pats.append((pid, body, 60.0, None, None))
    pats.append((9, planted, 20.0, None, None))
    t0 = time.perf_counter()
    r = _run(ep, dur, pats, min_coverage=0.5)
    assert time.perf_counter() - t0 < 2.0
    hit = [m for m in r if m.pattern_id == 9]
    assert len(hit) == 1 and abs(hit[0].start - 1500.0) < 0.3


def test_pattern_cut_off_at_file_head_matches_at_zero():
    rng = random.Random(9)
    ep, dur = _episode(rng, 60)
    pat = _rand_ints(rng, 160)
    ep[0:96] = pat[64:]
    r = _run(ep, dur, [(1, pat, 20.0, None, None)], min_coverage=0.5)
    assert len(r) == 1
    assert r[0].start == 0.0
    assert 0.55 <= r[0].coverage <= 0.65


def test_should_continue_false_stops_with_partial_results():
    rng = random.Random(10)
    ep, dur = _episode(rng, 60)
    a, b = _rand_ints(rng, 80), _rand_ints(rng, 80)
    _plant(ep, a, 100, rng, 1)
    _plant(ep, b, 300, rng, 1)
    calls = iter([True, False])
    r = _run(ep, dur, [(1, a, 10.0, None, None), (2, b, 10.0, None, None)],
             min_coverage=0.5, should_continue=lambda: next(calls))
    assert [m.pattern_id for m in r] == [1]


def test_empty_episode_returns_empty():
    assert _run([], 0.0, [(1, [1, 2, 3, 4, 5], 2.0, None, None)]) == []


def test_index_caps_positions_per_key_spread_evenly():
    arr = np.zeros(1000, dtype=np.uint32)
    idx = af._build_offset_index(arr, af.FINGERPRINT_KEY_BITS)
    kept = idx[0]
    assert len(kept) == af.FINGERPRINT_MAX_KEY_POSITIONS
    assert kept[0] == 0 and kept[-1] == 999
