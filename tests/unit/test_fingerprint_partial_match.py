"""Fingerprint matcher: partial matches of composite patterns under the minimum coverage."""
from unittest.mock import MagicMock

import numpy as np
import pytest

import audio_fingerprinter as af
from ad_detector import AdDetector
from audio_fingerprinter import explain_match, fingerprint_unmatchable_reason, match_fingerprints
from pattern_service import PatternService

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


def _full_ad_and_longer_composite(rng, tail_s):
    ad, tail = _ints(rng, 192), _ints(rng, int(tail_s * IPS))
    composite = np.concatenate([ad, tail, _ints(rng, 192), _ints(rng, 192)])
    ep = _ints(rng, 4800)
    _plant(rng, ep, np.concatenate([ad, tail]), START, 0.05)
    pats = [(1, ad, 24.0, None, None), (2, composite, len(composite) / IPS, None, None)]
    return match_fingerprints(ep, len(ep) / IPS, pats, **KW)


@pytest.mark.parametrize('seed', range(5))
def test_a_longer_partial_match_does_not_take_a_full_match_of_the_same_ad(seed):
    r = _full_ad_and_longer_composite(np.random.default_rng(seed), 8.0)
    full = [m for m in r if m.pattern_id == 1]
    assert len(full) == 1 and not full[0].partial and not full[0].clipped_remainder
    assert abs(full[0].start - START / IPS) <= 0.25 and abs(full[0].end - (START / IPS + 24.0)) <= 0.25
    rest = [m for m in r if m.pattern_id == 2]
    assert len(rest) == 1 and rest[0].partial and rest[0].clipped_remainder
    assert rest[0].start >= full[0].end - 1e-9 and rest[0].end - rest[0].start >= af.DEFAULT_MIN_AD_LENGTH_S


@pytest.mark.parametrize('seed', range(5))
def test_a_partial_remainder_under_the_minimum_ad_length_is_dropped(seed):
    r = _full_ad_and_longer_composite(np.random.default_rng(seed), 4.0)
    assert [m.pattern_id for m in r] == [1]


@pytest.mark.parametrize('seed', range(5))
def test_a_short_full_match_inside_a_partial_match_does_not_leave_the_ad_uncut(seed):
    rng = np.random.default_rng(seed)
    ad = _ints(rng, 400)
    composite = np.concatenate([ad, _ints(rng, 400), _ints(rng, 400)])
    ep = _ints(rng, 4800)
    _plant(rng, ep, ad, START, 0.05)
    pats = [(1, ad[176:224], 6.0, None, None), (2, composite, len(composite) / IPS, None, None)]
    r = match_fingerprints(ep, len(ep) / IPS, pats, **KW)
    assert [m.pattern_id for m in r] == [2] and r[0].partial and not r[0].clipped_remainder
    assert abs(r[0].start - START / IPS) <= 0.25 and abs(r[0].end - (START / IPS + 50.0)) <= 0.25


@pytest.mark.parametrize('first_s, partial', [(19.5, False), (20.5, True)])
def test_the_partial_evidence_floor_is_20_seconds(first_s, partial):
    for seed in range(5):
        ep, pattern = _composite_with_first_ad_alone(np.random.default_rng(seed), first_s)
        found, e = _scan_and_explain(ep, pattern)
        assert bool(found) == partial == e['partial']
        assert e['rejected_by'] == (None if partial else 'coverage')


def test_the_partial_floor_follows_its_constant(monkeypatch):
    ep, pattern = _composite_with_first_ad_alone(np.random.default_rng(0), 24.0)
    monkeypatch.setattr(af, 'FINGERPRINT_PARTIAL_EVIDENCE_S', 30.0)
    found, e = _scan_and_explain(ep, pattern)
    assert found == [] and e['rejected_by'] == 'coverage'


def _with_silence(rng, n):
    pat = _ints(rng, n)
    pat[n // 2:n // 2 + 16] = 7
    return pat


def test_full_minimum_coverage_does_not_refuse_a_long_distinctive_fingerprint():
    pat = _with_silence(np.random.default_rng(40), 480)
    assert af.fingerprint_coverage_ceiling(pat) < 1.0
    assert fingerprint_unmatchable_reason(pat, min_coverage=1.0) is None


def test_full_minimum_coverage_still_refuses_a_short_fingerprint_with_a_low_ceiling():
    pat = _with_silence(np.random.default_rng(41), 120)
    assert fingerprint_unmatchable_reason(pat, min_coverage=1.0).startswith('at most')


@pytest.mark.parametrize('partial', [True, False])
def test_a_partial_match_is_recorded_without_promotion(partial):
    detector = AdDetector.__new__(AdDetector)
    detector.pattern_service = MagicMock()
    match = af.FingerprintMatch(1, 10.0, 40.0, 0.9, similarity=0.9, coverage=0.3, partial=partial)
    detector._add_pattern_match(match, 'fingerprint', 'fingerprint', [], [], 'ep1')
    detector.pattern_service.record_pattern_match.assert_called_once_with(1, 'ep1', promote=not partial)


def test_record_pattern_match_without_promotion_still_counts_the_match():
    svc = PatternService(db=MagicMock())
    svc.check_for_promotion = MagicMock()
    svc.check_sponsor_global_promotion = MagicMock()
    svc.record_pattern_match(1, 'ep1', promote=False)
    svc.db.increment_pattern_match.assert_called_once_with(1)
    svc.check_for_promotion.assert_not_called()
    svc.check_sponsor_global_promotion.assert_not_called()
