"""Fingerprint matcher: real-format edges, adjacent ads, matchability, split runs and gate reporting."""
from pathlib import Path

import numpy as np
import pytest

import audio_fingerprinter as af
from audio_fingerprinter import AudioFingerprinter, explain_match, fingerprint_is_matchable, match_fingerprints
from config import FINGERPRINT_SETTING_RANGES

IPS = 8.0
KW = dict(strictness=0.65, min_coverage=0.5, gap_tolerance_s=3.5)
BITS = 1 << np.arange(32, dtype=np.int64)
FIXTURE = Path(__file__).resolve().parents[1] / 'fixtures' / 'fingerprints' / 'real_format.npz'


def _ints(rng, n):
    return rng.integers(0, 1 << 32, n, dtype=np.int64)


def _noisy(rng, pat, ber):
    return pat ^ ((rng.random((len(pat), 32)) < ber) * BITS).sum(axis=1)


def _plant(rng, ep, pat, idx, ber):
    ep[idx:idx + len(pat)] = _noisy(rng, pat, ber)


def _run(ep, pats, dur=None, **over):
    return match_fingerprints(ep, dur or len(ep) / IPS, pats, **{**KW, **over})


def _walk(rng, n, bits_per_step):
    steps = BITS[rng.integers(0, 32, (n, bits_per_step))].sum(axis=1) & 0xFFFFFFFF
    steps[0] = rng.integers(0, 1 << 32)
    return np.bitwise_xor.accumulate(steps)


def _fixture():
    z = np.load(FIXTURE)
    return tuple(z[k].astype(np.int64) for k in ('clean_a', 'degraded_a', 'clean_b'))


def _head_and_present_tail(clean, other, fps, seconds, s, u):
    """A pattern whose second half is clean_a from point s and whose first half is clean_b."""
    ln = int(seconds * fps)
    h = int(ln * 0.5)
    return np.concatenate([other[u:u + ln - h], clean[s:s + h]]), s / fps, (s + h) / fps


def test_partly_present_real_format_pattern_does_not_run_past_its_end():
    clean, degraded, other = _fixture()
    fps = len(degraded) / 180.0
    rng = np.random.default_rng(3)
    for _ in range(16):
        ln = int(20 * fps)
        present = int(ln * 0.75)
        s = int(rng.integers(20, len(clean) - ln - 20))
        u = int(rng.integers(20, len(other) - ln))
        pat = np.concatenate([clean[s:s + present], other[u:u + ln - present]])
        r = [m for m in _run(degraded, [(1, pat, 20.0, None, None)], dur=180.0)
             if m.start < (s + present) / fps and m.end > s / fps]
        assert len(r) == 1
        assert r[0].end - (s + present) / fps <= 0.5 and s / fps - r[0].start <= 0.5


@pytest.mark.parametrize('frac', [0.5, 0.6])
def test_a_short_agreeing_stretch_past_the_present_part_does_not_extend_the_match(frac):
    clean, degraded, other = _fixture()
    fps = len(degraded) / 180.0
    ln, short = int(30 * fps), int(2 * fps)
    h = int(ln * frac)
    for s, u in ((250, 350), (400, 600), (850, 850)):
        pat = np.concatenate([clean[s:s + h], other[u:u + ln - h]])
        ep = degraded.copy()
        # Two seconds of the absent part, six seconds past the present part, at the same alignment
        k = h + int(6 * fps)
        ep[s + k:s + k + short] = pat[k:k + short]
        r = match_fingerprints(ep, 180.0, [(1, pat, 30.0, None, None)], **KW)
        assert all(m.end - (s + h) / fps <= 0.5 for m in r)
        assert frac == 0.5 or len(r) == 1


@pytest.mark.parametrize('gap_s', [2.5, 3.0])
def test_a_short_island_two_seconds_past_the_present_part_is_not_an_edge(gap_s):
    clean, degraded, other = _fixture()
    fps = len(degraded) / 180.0
    ln, island = int(30 * fps), int(1.5 * fps)
    h = ln // 2
    for s, u in ((250, 350), (400, 600), (850, 850)):
        pat = np.concatenate([clean[s:s + h], other[u:u + ln - h]])
        ep = degraded.copy()
        k = h + int(gap_s * fps)
        ep[s + k:s + k + island] = pat[k:k + island]
        r = match_fingerprints(ep, 180.0, [(1, pat, 30.0, None, None)], **KW)
        assert r and all(m.end - (s + h) / fps <= 0.5 for m in r)


@pytest.mark.parametrize('s,u', [(1256, 290), (664, 292)])
def test_prefilter_keeps_the_true_alignment_of_a_half_present_pattern(s, u):
    clean, degraded, other = _fixture()
    fps = len(degraded) / 180.0
    pat, a, b = _head_and_present_tail(clean, other, fps, 10, s, u)
    pattern = (1, pat, 10.0, None, None)
    found = match_fingerprints(degraded, 180.0, [pattern], **KW)
    e = explain_match(degraded, 180.0, pattern, **KW)
    assert bool(found) == e['matched']
    for m in found:
        assert a - m.start <= 0.5 and m.end - b <= 0.5
        assert m.start == pytest.approx(e['start']) and m.end == pytest.approx(e['end'])


@pytest.mark.parametrize('seconds,shared_s', [(10, 0.5), (10, 1.0), (30, 1.0)])
def test_back_to_back_ads_stored_with_a_shared_edge_are_both_found(seconds, shared_s):
    rng = np.random.default_rng(seconds * 10 + int(shared_s * 10))
    for _ in range(10):
        ep = _ints(rng, 4800)
        ln, shared = int(seconds * IPS), int(shared_s * IPS)
        both = _ints(rng, 2 * ln - shared)
        _plant(rng, ep, both, 1000, 0.05)
        r = _run(ep, [(1, both[:ln], float(seconds), None, None),
                      (2, both[ln - shared:], float(seconds), None, None)])
        assert sorted(m.pattern_id for m in r) == [1, 2]
        assert r[0].end <= r[1].start


def _ad_partly_covered_by_a_longer_pattern(rng, other_s):
    """Two ads back to back, a pattern for the first and a longer one for the rest of it plus the second."""
    ep = _ints(rng, 4800)
    first, second = _ints(rng, 160), _ints(rng, 160)
    _plant(rng, ep, np.concatenate([first, second]), 1000, 0.05)
    k = int(other_s * IPS)
    return ep, [(1, first, 20.0, None, None), (9, np.concatenate([first[k:], second]), 40.0 - other_s, None, None)]


@pytest.mark.parametrize('other_s', [5.0, 6.0, 8.0])
def test_the_rest_of_an_ad_is_kept_when_a_longer_pattern_takes_most_of_it(other_s):
    rng = np.random.default_rng(int(other_s * 10))
    for _ in range(10):
        ep, pats = _ad_partly_covered_by_a_longer_pattern(rng, other_s)
        r = _run(ep, pats)
        assert [m.pattern_id for m in r] == [1, 9]
        assert abs(r[0].start - 125.0) <= 0.25 and r[0].end <= r[1].start
        assert r[1].start - r[0].end <= 0.25 and r[0].end - r[0].start >= 5.0


def test_a_remainder_shorter_than_the_minimum_ad_length_is_dropped():
    rng = np.random.default_rng(21)
    for _ in range(10):
        ep, pats = _ad_partly_covered_by_a_longer_pattern(rng, 4.0)
        assert [m.pattern_id for m in _run(ep, pats)] == [9]
        assert [m.pattern_id for m in _run(ep, pats, min_ad_length_s=3.0)] == [1, 9]


def test_a_kept_remainder_stays_inside_the_clipped_match():
    n = 300
    good = np.ones(n, dtype=bool)
    al = af._Alignment(1000, np.zeros(n, dtype=np.int64), good, np.ones(n), good, good, 8.0,
                       [(0, n)], span=(50, 200))
    runs = af._clipped_runs(al, [(1000, 1120)])
    assert runs and all(50 <= lo < hi <= 200 for lo, hi in runs)


@pytest.mark.parametrize('seconds', [3, 4, 5])
def test_short_matchable_patterns_find_their_own_audio(seconds):
    rng = np.random.default_rng(seconds)
    for ber in (0.0, 0.05, 0.10):
        for _ in range(10):
            ep = _ints(rng, 4800)
            pat = _ints(rng, int(seconds * IPS))
            assert fingerprint_is_matchable(pat)
            _plant(rng, ep, pat, 1000, ber)
            r = _run(ep, [(1, pat, float(seconds), None, None)])
            assert len(r) == 1 and abs(r[0].start - 125.0) <= 0.25


@pytest.mark.parametrize('seconds', [5, 6])
def test_short_patterns_are_found_at_twenty_percent_bit_error(seconds):
    rng = np.random.default_rng(30 + seconds)
    found = 0
    for _ in range(20):
        ep = _ints(rng, 4800)
        pat = _ints(rng, int(seconds * IPS))
        _plant(rng, ep, pat, 1000, 0.20)
        found += any(abs(m.start - 125.0) <= 0.5 for m in _run(ep, [(1, pat, float(seconds), None, None)]))
    assert found >= 19


def test_patterns_without_enough_distinctive_audio_are_not_matchable():
    rng = np.random.default_rng(6)
    assert not fingerprint_is_matchable([7] * 240)
    assert not fingerprint_is_matchable(_walk(rng, int(5 * IPS), 1))
    assert fingerprint_is_matchable(_walk(rng, int(10 * IPS), 1))
    # Mostly silence: coverage counts the silence as missing, so it could never reach the minimum
    pat = _ints(rng, 160)
    pat[20:110] = 7
    assert not fingerprint_is_matchable(pat)
    assert fingerprint_is_matchable(pat, min_coverage=0.3)
    assert af.fingerprint_unmatchable_reason(pat) is not None


def test_matchable_needs_a_coverage_ceiling_at_the_minimum_coverage():
    rng = np.random.default_rng(15)
    pat = _ints(rng, 160)
    pat[120:] = 7
    assert af.fingerprint_coverage_ceiling(pat) == 0.75
    assert fingerprint_is_matchable(pat, min_coverage=0.5)
    assert not fingerprint_is_matchable(pat, min_coverage=0.8)
    ep = _ints(rng, 4800)
    _plant(rng, ep, pat, 1000, 0.05)
    for cov, matchable in ((0.5, True), (0.8, False)):
        e = _explain(ep, pat, min_coverage=cov)
        assert e['coverage_ceiling'] == 0.75 and e['matchable'] is matchable
        assert e['matched'] is matchable


@pytest.mark.parametrize('seconds', [5, 6, 8])
def test_a_matchable_pattern_with_a_pause_is_found_in_its_own_audio(seconds):
    clean, _degraded, _other = _fixture()
    fps = len(clean) / 180.0
    rng = np.random.default_rng(40 + seconds)
    ln = int(seconds * fps)
    accepted = 0
    for _ in range(40):
        s = int(rng.integers(0, len(clean) - ln))
        pat = clean[s:s + ln].copy()
        k = int(rng.uniform(1.5, 0.6 * seconds) * fps)
        p0 = int(rng.integers(0, ln - k))
        pat[p0:p0 + k] = pat[p0]
        matchable = fingerprint_is_matchable(pat, min_coverage=0.5)
        assert _explain(clean, pat)['matchable'] is matchable
        if not matchable:
            continue
        accepted += 1
        ep = clean.copy()
        ep[s:s + ln] = pat
        assert any(m.start < (s + ln) / fps and m.end > s / fps
                   for m in match_fingerprints(ep, 180.0, [(1, pat, float(seconds), None, None)], **KW))
    assert accepted >= 5


def test_a_gap_longer_than_the_tolerance_splits_the_match():
    rng = np.random.default_rng(8)
    for _ in range(10):
        ep = _ints(rng, 4800)
        pat = _ints(rng, 240)
        _plant(rng, ep, pat[:100], 1000, 0.05)
        _plant(rng, ep, pat[140:], 1140, 0.05)
        assert _run(ep, [(1, pat, 30.0, None, None)]) == []
        r = _run(ep, [(1, pat, 30.0, None, None)], min_coverage=0.4)
        assert [(round(m.start * 4) / 4, round(m.end * 4) / 4) for m in r] == [(125.0, 137.5), (142.5, 155.0)]


def test_a_dropout_within_the_tolerance_is_bridged():
    rng = np.random.default_rng(19)
    for _ in range(10):
        ep = _ints(rng, 4800)
        pat = _ints(rng, 240)
        _plant(rng, ep, pat[:100], 1000, 0.05)
        _plant(rng, ep, pat[124:], 1124, 0.05)
        r = _run(ep, [(1, pat, 30.0, None, None)])
        assert len(r) == 1
        assert abs(r[0].start - 125.0) <= 0.25 and abs(r[0].end - 155.0) <= 0.25


@pytest.mark.parametrize('share', [0.25, 0.3, 0.4])
def test_head_and_tail_around_unrelated_audio_are_not_one_match(share):
    clean, degraded, other = _fixture()
    fps = len(degraded) / 180.0
    ln = int(30 * fps)
    h = int(ln * share)
    rng = np.random.default_rng(20)
    for _ in range(10):
        s, u = int(rng.integers(20, len(clean) - ln - 20)), int(rng.integers(20, len(other) - ln))
        pat = np.concatenate([clean[s:s + h], other[u:u + ln - 2 * h], clean[s + ln - h:s + ln]])
        for cov in (0.3, 0.5):
            for m in _run(degraded, [(1, pat, 30.0, None, None)], dur=180.0, min_coverage=cov):
                assert m.end <= (s + h) / fps + 0.5 or m.start >= (s + ln - h) / fps - 0.5


def test_short_fragments_at_one_alignment_do_not_add_up():
    rng = np.random.default_rng(9)
    for _ in range(10):
        ep = _ints(rng, 4800)
        pat = _ints(rng, 480)
        # Six-point fragments every five seconds, at the alignment the full pattern would have
        for start in range(0, 480, 40):
            _plant(rng, ep, pat[start:start + 6], 1000 + start, 0.0)
        assert _run(ep, [(1, pat, 60.0, None, None)], min_coverage=0.1) == []


def test_strictness_gates_similarity_not_the_per_point_threshold():
    rng = np.random.default_rng(10)
    ep = _ints(rng, 4800)
    pat = _ints(rng, 240)
    _plant(rng, ep, pat, 1000, 0.25)
    assert len(_run(ep, [(1, pat, 30.0, None, None)], strictness=0.70)) == 1
    assert _run(ep, [(1, pat, 30.0, None, None)], strictness=0.80) == []
    unrelated = [(i, _ints(rng, int(rng.uniform(5, 30) * IPS)), 0.0, None, None) for i in range(200)]
    assert _run(_ints(rng, int(3600 * IPS)), unrelated, strictness=0.40) == []


def test_key_subsets_are_fixed():
    assert len(af.FINGERPRINT_KEY_SUBSETS) == 16
    assert all(len(set(s)) == af.FINGERPRINT_KEY_BITS and all(0 <= b < 32 for b in s)
               for s in af.FINGERPRINT_KEY_SUBSETS)
    keys = af._table_keys(np.array([0x12345678], dtype=np.uint32))[0].tolist()
    assert keys == [1596, 7116, 8900, 13926, 18468, 22541, 24708, 31299,
                    33234, 39453, 43720, 46689, 50337, 55314, 60108, 64516]


def test_candidate_cap_is_logged_once(caplog, monkeypatch):
    monkeypatch.setattr(af, 'FINGERPRINT_MAX_CANDIDATES', 3)
    rng = np.random.default_rng(11)
    ep = _ints(rng, 4800)
    pat = _ints(rng, 80)
    for k in range(6):
        _plant(rng, ep, pat, 200 + k * 600, 0.05)
    with caplog.at_level('DEBUG', logger='podcast.fingerprint'):
        r = _run(ep, [(42, pat, 10.0, None, None)])
    assert len(r) == 3
    capped = [x.getMessage() for x in caplog.records if 'candidate alignments' in x.getMessage()]
    assert len(capped) == 1 and '42' in capped[0] and '6' in capped[0]


def test_lowest_strictness_rejects_unrelated_real_format_windows_in_the_fallback():
    clean, degraded, other = _fixture()
    ln = int(10 * len(degraded) / 180.0)
    lowest = FINGERPRINT_SETTING_RANGES['fingerprint_match_strictness'][1]
    fp = AudioFingerprinter.__new__(AudioFingerprinter)
    rng = np.random.default_rng(14)
    passed = 0
    for a in (degraded, clean):
        for _ in range(500):
            i, j = int(rng.integers(0, len(a) - ln)), int(rng.integers(0, len(other) - ln))
            passed += fp._calculate_similarity(a[i:i + ln].tolist(), other[j:j + ln].tolist()) >= lowest
    assert passed / 1000 < 0.02


def _explain(ep, pat, **over):
    return explain_match(ep, len(ep) / IPS, (1, pat, len(pat) / IPS, None, None), **{**KW, **over})


def test_explain_reports_a_match():
    rng = np.random.default_rng(12)
    ep = _ints(rng, 4800)
    pat = _ints(rng, 240)
    _plant(rng, ep, pat, 1000, 0.10)
    e = _explain(ep, pat)
    assert e['matched'] and e['rejected_by'] is None
    assert abs(e['start'] - 125.0) <= 0.25 and abs(e['end'] - 155.0) <= 0.25
    assert e['coverage'] >= 0.95 and e['evidence_s'] >= e['evidence_needed_s']
    assert e['diff_p50'] <= e['diff_p90'] and e['good_share'] >= e['edge_good_share']
    assert e['runs_counted'] == 1 and e['degraded_stretches'] == 0


def test_explain_counts_degraded_stretches_inside_the_match_only():
    rng = np.random.default_rng(16)
    ep = _ints(rng, 4800)
    pat = _ints(rng, 240)
    _plant(rng, ep, pat[:160], 1000, 0.05)
    e = _explain(ep, pat)
    assert e['matched'] and abs(e['end'] - 145.0) <= 0.25
    assert e['degraded_stretches'] == 0 and e['longest_degraded_s'] == 0


def test_close_evidence_is_counted_in_episode_seconds():
    rng = np.random.default_rng(17)
    ep = _ints(rng, 4800)
    pat = _ints(rng, 240)
    _plant(rng, ep, pat, 1000, 0.10)
    for seconds in (600.0, 300.0):
        e = explain_match(ep, seconds, (1, pat, 30.0, None, None), **KW)
        assert e['close_evidence_needed_s'] == pytest.approx(af.FINGERPRINT_CLOSE_EVIDENCE_S)
        assert e['evidence_needed_s'] == pytest.approx(af.FINGERPRINT_MIN_EVIDENCE_S)


def test_novelty_is_computed_once_per_pattern(monkeypatch):
    calls = []
    novelty = af._novelty
    monkeypatch.setattr(af, '_novelty', lambda *a: calls.append(1) or novelty(*a))
    rng = np.random.default_rng(18)
    _run(_ints(rng, 4800), [(i, _ints(rng, 80), 10.0, None, None) for i in range(3)])
    assert len(calls) == 3


def test_explain_names_the_rejecting_gate():
    rng = np.random.default_rng(13)
    ep = _ints(rng, 4800)
    pat = _ints(rng, 160)
    assert _explain(np.full(4800, 7), pat)['rejected_by'] == 'candidates'
    assert _explain(ep, np.full(160, 7))['rejected_by'] == 'not_distinctive'
    _plant(rng, ep, pat[:100], 1000, 0.05)
    e = _explain(ep, pat, min_coverage=0.9)
    assert e['rejected_by'] == 'coverage' and not e['matched'] and e['coverage'] < 0.9
    short = _ints(rng, 80)
    _plant(rng, ep, short[:44], 3000, 0.05)
    assert _explain(ep, short)['rejected_by'] == 'evidence'
    noisy = _ints(rng, 160)
    _plant(rng, ep, noisy, 2000, 0.10)
    e = _explain(ep, noisy, strictness=0.95)
    assert e['rejected_by'] == 'similarity' and e['similarity'] < 0.95
