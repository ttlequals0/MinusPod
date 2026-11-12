"""Matcher results are identical with the pattern cache cold or warm, and the offset prefilter is chunk-safe."""
import threading
from types import SimpleNamespace

import numpy as np
import pytest

import audio_fingerprinter as af
from audio_fingerprinter import AudioFingerprinter, explain_match, fingerprint_is_matchable, match_fingerprints

IPS = 8.0
KW = dict(strictness=0.65, min_coverage=0.5, gap_tolerance_s=3.5)
BITS = 1 << np.arange(32, dtype=np.int64)


@pytest.fixture(autouse=True)
def cold_cache():
    af._PATTERN_CACHE.clear()
    yield
    af._PATTERN_CACHE.clear()


def _ints(rng, n):
    return rng.integers(0, 1 << 32, n, dtype=np.int64)


def _noisy(rng, pat, ber):
    return pat ^ ((rng.random((len(pat), 32)) < ber) * BITS).sum(axis=1)


def _world(seed):
    """An episode with noisy plants of three patterns, plus a constant and a short pattern."""
    rng = np.random.default_rng(seed)
    ep = _ints(rng, int(240 * IPS))
    patterns = []
    for pid, (seconds, at) in enumerate([(14, 20), (22, 90), (10, 170)]):
        pat = _ints(rng, int(seconds * IPS))
        start = int(at * IPS)
        ep[start:start + len(pat)] = _noisy(rng, pat, 0.08)
        patterns.append((pid, pat, seconds, f'sponsor{pid}', None))
    patterns.append((3, np.full(int(12 * IPS), 7, dtype=np.int64), 12.0, None, None))
    patterns.append((4, _ints(rng, int(2 * IPS)), 2.0, None, None))
    return ep, patterns


def _scan(ep, patterns):
    return match_fingerprints(ep, len(ep) / IPS, patterns, **KW)


@pytest.mark.parametrize('seed', range(8))
def test_scan_is_identical_cold_and_warm(seed):
    ep, patterns = _world(seed)
    cold = _scan(ep, patterns)
    assert cold
    assert len(af._PATTERN_CACHE) > 0
    assert _scan(ep, patterns) == cold
    af._PATTERN_CACHE.clear()
    assert _scan(ep, patterns) == cold


@pytest.mark.parametrize('seed', range(4))
def test_matchable_verdict_and_explanation_are_identical_cold_and_warm(seed):
    ep, patterns = _world(seed)
    for pid, pat, seconds, sponsor, category in patterns:
        for coverage in (None, 0.5, 0.9):
            af._PATTERN_CACHE.clear()
            cold = af.fingerprint_unmatchable_reason(pat, min_coverage=coverage)
            assert af.fingerprint_unmatchable_reason(pat, min_coverage=coverage) == cold
        af._PATTERN_CACHE.clear()
        cold_explained = explain_match(ep, len(ep) / IPS, patterns[pid], **KW)
        assert explain_match(ep, len(ep) / IPS, patterns[pid], **KW) == cold_explained
        assert af.fingerprint_coverage_ceiling(pat) == af.fingerprint_coverage_ceiling(pat)


def test_a_rebuilt_pattern_is_seen_at_once():
    rng = np.random.default_rng(1)
    varied, constant = _ints(rng, int(12 * IPS)), np.full(int(12 * IPS), 7, dtype=np.int64)
    assert fingerprint_is_matchable(varied) is True
    assert fingerprint_is_matchable(constant) is False
    assert fingerprint_is_matchable(varied) is True
    assert fingerprint_is_matchable(constant) is False


def test_decoded_points_are_cached_and_a_changed_fingerprint_decodes_again(monkeypatch):
    calls = []
    table = {b'AAA': [1, 2, 3], b'BBB': [4, 5, 6]}

    def decode(data):
        calls.append(bytes(data))
        return table[bytes(data)], 1
    monkeypatch.setattr(af, 'acoustid', SimpleNamespace(chromaprint=SimpleNamespace(decode_fingerprint=decode)))
    fp = AudioFingerprinter.__new__(AudioFingerprinter)
    first = fp._decode_known_fingerprints([(9, 'AAA', 1.0, 's', None)])
    assert fp._decode_known_fingerprints([(9, 'AAA', 1.0, 's', None)]) == first
    assert calls == [b'AAA']
    rebuilt = fp._decode_known_fingerprints([(9, 'BBB', 1.0, 's', None)])
    assert rebuilt[0][1] == [4, 5, 6] and first[0][1] == [1, 2, 3]
    assert af.decode_fingerprint_ints('AAA') == [1, 2, 3]
    assert calls == [b'AAA', b'BBB']


def test_explain_match_reuses_the_cached_profile(monkeypatch):
    ep, patterns = _world(0)
    explain_match(ep, len(ep) / IPS, patterns[0], **KW)
    calls = []
    real = af._informative_points
    monkeypatch.setattr(af, '_informative_points', lambda arr: calls.append(1) or real(arr))
    explain_match(ep, len(ep) / IPS, patterns[0], **KW)
    assert fingerprint_is_matchable(patterns[0][1], min_coverage=0.5) in (True, False)
    assert calls == []


def test_the_cache_is_bounded_and_thread_safe():
    cache = af._PatternCache(8)
    errors = []

    def work(base):
        try:
            for i in range(200):
                assert cache.get_or_compute((base, i % 20), lambda i=i: i % 20) == i % 20
        except Exception as e:
            errors.append(e)
    threads = [threading.Thread(target=work, args=(t,)) for t in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == [] and len(cache) <= 8


def _unchunked_passing(ep_arr, pat_arr, informative, offsets, min_good):
    if len(offsets) == 0:
        return offsets
    j = np.flatnonzero(informative)
    idx = offsets[:, None] + j[None, :]
    inside = (idx >= 0) & (idx < len(ep_arr))
    diff = af._popcount32(ep_arr[np.clip(idx, 0, len(ep_arr) - 1)] ^ pat_arr[j])
    return offsets[((diff <= af.FINGERPRINT_GOOD_BITS) & inside).sum(axis=1) >= min_good]


def test_chunked_prefilter_equals_the_unchunked_form_on_seeded_cases():
    cases = 0
    for seed in range(250):
        rng = np.random.default_rng(seed)
        ep_len = int(rng.integers(40, 900))
        pat_len = int(rng.integers(8, 120))
        ep = _ints(rng, ep_len).astype(np.uint32)
        pat = _ints(rng, pat_len).astype(np.uint32)
        if seed % 3 == 0:
            at = int(rng.integers(0, max(1, ep_len - pat_len)))
            ep[at:at + pat_len] = _noisy(rng, pat.astype(np.int64), 0.05).astype(np.uint32)[:ep_len - at]
        if seed % 5 == 0:
            pat[: pat_len // 3] = 5
        informative = af._informative_points(pat)
        count = int(rng.choice([0, 1, 63, 64, 65, 128, 200, 311]))
        offsets = rng.integers(-pat_len, ep_len + 5, count).astype(np.int64)
        min_good = float(rng.choice([0.0, 2.5, pat_len * 0.3, pat_len * 0.8, pat_len + 1.0]))
        expected = _unchunked_passing(ep, pat, informative, offsets, min_good)
        got = af._passing_alignments(ep, pat, informative, offsets, min_good)
        assert got.dtype == expected.dtype and got.tolist() == expected.tolist()
        cases += 1
    assert cases == 250
