"""Tests for defined-pattern match ownership and absorbed match credit."""
from tests.app_bootstrap import bootstrap
bootstrap('merge_matches_tiers_test_')

from text_pattern_matcher import TextPatternMatcher, TextMatch


def _match(pid, defined, category, conf=1.0, start=100.0, end=160.0):
    return TextMatch(pattern_id=pid, start=start, end=end, confidence=conf,
                      sponsor='Acme', match_type='both', category=category,
                      defined=defined)


def test_defined_match_owns_overlap_regardless_of_order():
    m = TextPatternMatcher(db=None)
    auto = _match(628, False, 'cross_promo')
    user = _match(622, True, None, conf=0.95)
    merged = m._merge_matches([auto, user])
    assert len(merged) == 1
    assert merged[0].pattern_id == 622
    assert merged[0].category is None
    assert 628 in merged[0].absorbed_ids


def test_two_auto_matches_keep_confidence_winner():
    m = TextPatternMatcher(db=None)
    a = _match(1, False, 'sponsor', conf=0.9)
    b = _match(2, False, 'sponsor', conf=0.95)
    merged = m._merge_matches([a, b])
    assert merged[0].pattern_id == 2
