"""Fingerprint matcher: partial matches of composite patterns under the minimum coverage."""
import numpy as np
import pytest

import audio_fingerprinter as af
from audio_fingerprinter import explain_match, match_fingerprints

IPS = 8.0
KW = dict(strictness=0.65, min_coverage=0.5, gap_tolerance_s=3.5)
BITS = 1 << np.arange(32, dtype=np.int64)
START = 1000


def _ints(rng, n):
    return rng.integers(0, 1 << 32, n, dtype=np.int64)


def _plant(rng, ep, pat, idx, ber):
    ep[idx:idx + len(pat)] = pat ^ ((rng.random((len(pat), 32)) < ber) * BITS).sum(axis=1)


def _scan_and_explain(ep, pattern, **over):
    kw = {**KW, **over}
    found = [m for m in match_fingerprints(ep, len(ep) / IPS, [pattern], **kw) if m.pattern_id == pattern[0]]
    return found, explain_match(ep, len(ep) / IPS, pattern, **kw)


def _composite_with_first_ad_alone(rng, first_s, ber=0.05):
    ads = [_ints(rng, int(first_s * IPS)), _ints(rng, 192), _ints(rng, 192)]
    pat = np.concatenate(ads)
    ep = _ints(rng, 4800)
    _plant(rng, ep, ads[0], START, ber)
    return ep, (1, pat, len(pat) / IPS, None, None)


@pytest.mark.parametrize('seed', range(5))
def test_one_recurring_ad_of_a_composite_pattern_matches_as_partial(seed):
    ep, pattern = _composite_with_first_ad_alone(np.random.default_rng(seed), 24.0)
    found, e = _scan_and_explain(ep, pattern)
    assert len(found) == 1 and found[0].partial
    m = found[0]
    assert abs(m.start - START / IPS) <= 0.25 and abs(m.end - (START / IPS + 24.0)) <= 0.25
    assert m.coverage < KW['min_coverage']
    assert e['matched'] and e['partial'] and e['rejected_by'] is None
    assert e['evidence_s'] >= af.FINGERPRINT_PARTIAL_EVIDENCE_S
    assert abs(e['start'] - m.start) <= 0.25 and abs(e['end'] - m.end) <= 0.25


@pytest.mark.parametrize('seed', range(5))
def test_a_recurring_ad_under_the_partial_evidence_floor_is_rejected_by_coverage(seed):
    ep, pattern = _composite_with_first_ad_alone(np.random.default_rng(seed), 19.0)
    found, e = _scan_and_explain(ep, pattern)
    assert found == []
    assert e['rejected_by'] == 'coverage' and not e['matched'] and not e['partial']


@pytest.mark.parametrize('seed', range(5))
def test_a_shared_head_followed_by_different_audio_is_rejected(seed):
    rng = np.random.default_rng(seed)
    head = _ints(rng, 80)
    pat = np.concatenate([head, _ints(rng, 192), _ints(rng, 192)])
    ep = _ints(rng, 4800)
    _plant(rng, ep, head, START, 0.05)
    found, e = _scan_and_explain(ep, (1, pat, len(pat) / IPS, None, None))
    assert found == []
    assert e['rejected_by'] == 'coverage' and not e['partial']


@pytest.mark.parametrize('seed', range(5))
def test_a_partial_alignment_below_strictness_is_rejected(seed):
    ep, pattern = _composite_with_first_ad_alone(np.random.default_rng(seed), 24.0, ber=0.2)
    found, e = _scan_and_explain(ep, pattern)
    assert len(found) == 1 and found[0].partial and e['partial']
    found, e = _scan_and_explain(ep, pattern, strictness=0.9)
    assert found == []
    assert e['rejected_by'] == 'similarity' and not e['matched'] and not e['partial']


@pytest.mark.parametrize('seed', range(5))
def test_a_longer_partial_match_does_not_take_a_full_match_of_the_same_ad(seed):
    rng = np.random.default_rng(seed)
    ad, tail = _ints(rng, 192), _ints(rng, 64)
    composite = np.concatenate([ad, tail, _ints(rng, 192), _ints(rng, 192)])
    ep = _ints(rng, 4800)
    _plant(rng, ep, np.concatenate([ad, tail]), START, 0.05)
    pats = [(1, ad, 24.0, None, None), (2, composite, len(composite) / IPS, None, None)]
    r = match_fingerprints(ep, len(ep) / IPS, pats, **KW)
    full = [m for m in r if m.pattern_id == 1]
    assert len(full) == 1 and not full[0].partial and not full[0].clipped_remainder
    assert abs(full[0].start - START / IPS) <= 0.25 and abs(full[0].end - (START / IPS + 24.0)) <= 0.25
    for m in r:
        if m.pattern_id == 2:
            assert m.partial and m.clipped_remainder and m.start >= full[0].end - 1e-9
