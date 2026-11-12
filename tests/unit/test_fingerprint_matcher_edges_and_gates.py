"""Fingerprint matcher: real-format edges, adjacent ads, matchability, split runs and gate reporting."""
from pathlib import Path

import numpy as np
import pytest

import audio_fingerprinter as af
from audio_fingerprinter import explain_match, fingerprint_is_matchable, match_fingerprints

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


def test_partly_present_real_format_pattern_does_not_run_past_its_end():
    z = np.load(FIXTURE)
    clean, degraded, other = (z[k].astype(np.int64) for k in ('clean_a', 'degraded_a', 'clean_b'))
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


def test_ad_split_by_a_long_degraded_stretch_is_one_match():
    rng = np.random.default_rng(8)
    for _ in range(10):
        ep = _ints(rng, 4800)
        pat = _ints(rng, 240)
        _plant(rng, ep, pat[:100], 1000, 0.05)
        _plant(rng, ep, pat[140:], 1140, 0.05)
        r = _run(ep, [(1, pat, 30.0, None, None)])
        assert len(r) == 1
        assert abs(r[0].start - 125.0) <= 0.25 and abs(r[0].end - 155.0) <= 0.25
        assert 0.75 <= r[0].coverage <= 0.85


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
