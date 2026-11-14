"""Fingerprint matcher accuracy: run edges, evidence coverage, candidates, repeats and ranking."""
import numpy as np
import pytest

import audio_fingerprinter as af
from audio_fingerprinter import match_fingerprints

IPS = 8.0
KW = dict(strictness=0.65, min_coverage=0.5, gap_tolerance_s=3.5)
SILENCE = 0x5A5A5A5A
BITS = 1 << np.arange(32, dtype=np.int64)


def _ints(rng, n):
    return rng.integers(0, 1 << 32, n, dtype=np.int64)


def _noisy(rng, pat, ber):
    flips = ((rng.random((len(pat), 32)) < ber) * BITS).sum(axis=1)
    return pat ^ flips


def _plant(rng, ep, pat, idx, ber):
    ep[idx:idx + len(pat)] = _noisy(rng, pat, ber)


def _run(ep, pats, **over):
    return match_fingerprints(ep, len(ep) / IPS, pats, **{**KW, **over})


@pytest.mark.parametrize('side', ['first', 'second'])
def test_half_present_pattern_stops_at_the_last_matching_point(side):
    rng = np.random.default_rng(8 if side == 'first' else 9)
    for _ in range(25):
        ep = _ints(rng, 4800)
        pat = _ints(rng, 240)
        if side == 'first':
            _plant(rng, ep, pat[:120], 1000, 0.05)
        else:
            _plant(rng, ep, pat[120:], 1120, 0.05)
        r = _run(ep, [(1, pat, 30.0, None, None)], min_coverage=0.4)
        assert len(r) == 1
        edge = r[0].end if side == 'first' else r[0].start
        assert abs(edge - 140.0) <= 0.5
        assert 0.45 <= r[0].coverage <= 0.51


def test_jingle_shared_with_the_show_does_not_match():
    rng = np.random.default_rng(3)
    spurious = 0
    for frac, pos in ((0.3, 'head'), (0.4, 'tail'), (0.4, 'head')):
        ep = _ints(rng, int(3600 * IPS))
        jingle = _ints(rng, int(10 * frac * IPS))
        for k in range(30):
            _plant(rng, ep, jingle, 1000 + k * 900, 0.05)
        body = _ints(rng, 80 - len(jingle))
        pat = np.concatenate([jingle, body] if pos == 'head' else [body, jingle])
        spurious += len(_run(ep, [(1, pat, 10.0, None, None)]))
    assert spurious == 0


def test_silence_head_does_not_match_bare_silence_but_the_ad_still_does():
    rng = np.random.default_rng(4)
    ep = _ints(rng, int(3600 * IPS))
    for k in range(20):
        ep[2000 + k * 1300:2000 + k * 1300 + 48] = SILENCE
    pat = np.concatenate([np.full(40, SILENCE), _ints(rng, 120)])
    assert _run(ep, [(1, pat, 20.0, None, None)]) == []
    _plant(rng, ep, pat, 27000, 0.05)
    ep[27000:27040] = SILENCE
    r = _run(ep, [(1, pat, 20.0, None, None)])
    assert len(r) == 1
    assert abs(r[0].start - 27040 / IPS) <= 0.25 and abs(r[0].end - 27160 / IPS) <= 0.25
    assert 0.7 <= r[0].coverage <= 0.76


@pytest.mark.parametrize('fill', ['noise', 'silence'])
def test_short_dropout_inside_an_ad_is_one_match(fill):
    rng = np.random.default_rng(5)
    ep = _ints(rng, 4800)
    pat = _ints(rng, 240)
    _plant(rng, ep, pat, 1000, 0.05)
    ep[1100:1124] = _ints(rng, 24) if fill == 'noise' else SILENCE
    r = _run(ep, [(1, pat, 30.0, None, None)])
    assert len(r) == 1
    assert abs(r[0].start - 125.0) <= 0.25 and abs(r[0].end - 155.0) <= 0.25
    assert 0.85 <= r[0].coverage <= 0.91


@pytest.mark.parametrize('seconds,ber', [(10, 0.25), (10, 0.30), (30, 0.30), (60, 0.30)])
def test_high_bit_error_alignments_are_found(seconds, ber):
    rng = np.random.default_rng(int(seconds * 100 + ber * 100))
    found = 0
    for _ in range(10):
        ep = _ints(rng, int(1800 * IPS))
        pat = _ints(rng, int(seconds * IPS))
        idx = 16 * int(rng.integers(20, 800))
        _plant(rng, ep, pat, idx, ber)
        r = _run(ep, [(1, pat, float(seconds), None, None)])
        found += any(m.start < idx / IPS + seconds and m.end > idx / IPS for m in r)
    # A 10 s ad at 30 percent error can fall short of the close-agreement floor
    assert found >= (9 if (seconds, ber) == (10, 0.30) else 10)


def test_unaligned_offsets_are_found_at_twenty_percent_bit_error():
    rng = np.random.default_rng(12)
    for _ in range(10):
        ep = _ints(rng, int(1800 * IPS))
        pat = _ints(rng, 80)
        idx = 16 * int(rng.integers(20, 800)) + int(rng.integers(1, 16))
        _plant(rng, ep, pat, idx, 0.20)
        r = _run(ep, [(1, pat, 10.0, None, None)])
        assert any(abs(m.start - idx / IPS) <= 0.5 for m in r)


@pytest.mark.parametrize('kind', ['random', 'correlated'])
def test_unrelated_patterns_do_not_match(kind):
    rng = np.random.default_rng(13)

    def gen(n):
        if kind == 'random':
            return _ints(rng, n)
        steps = BITS[rng.integers(0, 32, (n, 3))].sum(axis=1) & 0xFFFFFFFF
        steps[0] = rng.integers(0, 1 << 32)
        return np.bitwise_xor.accumulate(steps)
    ep = gen(int(2 * 3600 * IPS))
    pats = [(i, gen(int(rng.uniform(5, 10) * IPS)), 0.0, None, None) for i in range(300)]
    assert _run(ep, pats) == []


def test_slowly_changing_unrelated_audio_does_not_match():
    rng = np.random.default_rng(14)

    def walk(n):
        steps = BITS[rng.integers(0, 32, n)]
        steps[0] = rng.integers(0, 1 << 32)
        return np.bitwise_xor.accumulate(steps)
    ep = walk(int(3600 * IPS))
    pats = [(i, walk(int(rng.uniform(5, 30) * IPS)), 0.0, None, None) for i in range(300)]
    assert _run(ep, pats) == []


def test_slowly_changing_ad_is_still_found():
    rng = np.random.default_rng(15)
    steps = BITS[rng.integers(0, 32, 240)]
    steps[0] = rng.integers(0, 1 << 32)
    pat = np.bitwise_xor.accumulate(steps)
    ep = _ints(rng, 4800)
    _plant(rng, ep, pat, 1000, 0.05)
    r = _run(ep, [(1, pat, 30.0, None, None)])
    assert len(r) == 1 and abs(r[0].start - 125.0) <= 0.25 and abs(r[0].end - 155.0) <= 0.25


@pytest.mark.parametrize('distinct', [64, 256])
def test_overfull_keys_do_not_hide_matches(distinct):
    rng = np.random.default_rng(distinct)
    tops = rng.integers(0, 1 << 16, distinct)

    def gen(n):
        return (tops[rng.integers(0, distinct, n)] << 16) | rng.integers(0, 1 << 16, n)
    ep = gen(int(3600 * IPS))
    pats = [(i, gen(int(rng.uniform(5, 30) * IPS)), 0.0, None, None) for i in range(20)]
    for i, p in enumerate(pats):
        _plant(rng, ep, p[1], 2000 + i * 1200, 0.05)
    r = _run(ep, pats)
    assert sorted(m.pattern_id for m in r if abs(m.start - (2000 + m.pattern_id * 1200) / IPS) <= 0.5) == list(range(20))


def test_every_occurrence_of_a_repeated_pattern_is_found():
    rng = np.random.default_rng(20)
    ep = _ints(rng, int(3600 * IPS))
    pat = _ints(rng, 160)
    starts = [500 + k * 1400 + k for k in range(20)]
    for idx in starts:
        _plant(rng, ep, pat, idx, 0.05)
    r = _run(ep, [(1, pat, 20.0, None, None)])
    assert [round(m.start * IPS) for m in r] == starts


@pytest.mark.parametrize('planted,winner', [('full', 1), ('cut', 2)])
def test_overlap_goes_to_the_pattern_with_more_evidence(planted, winner):
    rng = np.random.default_rng(31 if planted == 'full' else 32)
    for _ in range(25):
        ep = _ints(rng, 4800)
        full = _ints(rng, 240)
        _plant(rng, ep, full if planted == 'full' else full[:120], 1000, 0.05)
        r = _run(ep, [(1, full, 30.0, None, None), (2, full[:120].copy(), 15.0, None, None)])
        assert [m.pattern_id for m in r] == [winner]


def test_best_run_wins_over_a_longer_failing_one():
    rng = np.random.default_rng(71)
    for _ in range(10):
        ep = _ints(rng, 4800)
        pat = _ints(rng, 320)
        _plant(rng, ep, pat[:160], 1000, 0.37)
        _plant(rng, ep, pat[200:296], 1200, 0.05)
        r = _run(ep, [(1, pat, 40.0, None, None)], min_coverage=0.25)
        assert len(r) == 1
        assert abs(r[0].start - 150.0) <= 0.5 and abs(r[0].end - 162.0) <= 0.5


def test_a_few_seconds_of_agreement_is_not_a_match():
    rng = np.random.default_rng(40)
    ep = _ints(rng, 4800)
    pat = _ints(rng, 80)
    _plant(rng, ep, pat[:44], 1000, 0.05)
    assert _run(ep, [(1, pat, 10.0, None, None)]) == []
    _plant(rng, ep, pat[:52], 1000, 0.05)
    assert len(_run(ep, [(1, pat, 10.0, None, None)])) == 1


def test_short_pattern_needs_nearly_all_of_it():
    rng = np.random.default_rng(41)
    ep = _ints(rng, 4800)
    pat = _ints(rng, 40)
    _plant(rng, ep, pat[:34], 1000, 0.0)
    assert _run(ep, [(1, pat, 5.0, None, None)]) == []
    _plant(rng, ep, pat, 1000, 0.0)
    assert len(_run(ep, [(1, pat, 5.0, None, None)])) == 1


def test_scan_measures_few_alignments_on_unrelated_audio():
    rng = np.random.default_rng(50)
    ep = _ints(rng, int(3600 * IPS))
    pats = [(i, _ints(rng, int(rng.uniform(5, 60) * IPS)), 0.0, None, None) for i in range(200)]
    r, measured = af._scan_fingerprints(ep, 3600.0, pats, **KW)
    assert r == [] and measured <= 5
