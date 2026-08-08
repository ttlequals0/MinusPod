"""Tests for action-aware and cut-status-aware ad merging in AdValidator."""
from tests.app_bootstrap import bootstrap
bootstrap('merge_close_ads_actions_test_')

from ad_validator import AdValidator, ValidationResult


def _validator():
    return AdValidator.__new__(AdValidator)


def test_no_merge_across_resolved_actions():
    ads = [
        {'start': 1490.0, 'end': 1531.6, 'confidence': 0.9, 'category': 'self_promo'},
        {'start': 1533.4, 'end': 1595.0, 'confidence': 1.0, 'category': 'sponsor',
         '_saved_was_cut': True},
    ]
    merged = _validator()._merge_close_ads(
        ads, ValidationResult(ads=[]),
        actions_map={'self_promo': 'keep', 'sponsor': 'remove'})
    assert len(merged) == 2


def test_no_merge_across_saved_cut_status_even_same_action():
    ads = [
        {'start': 100.0, 'end': 130.0, 'confidence': 0.9, 'category': 'sponsor'},
        {'start': 131.0, 'end': 160.0, 'confidence': 0.9, 'category': 'sponsor',
         '_saved_was_cut': True},
    ]
    merged = _validator()._merge_close_ads(ads, ValidationResult(ads=[]), actions_map=None)
    assert len(merged) == 2


def test_close_same_action_markers_still_merge():
    ads = [
        {'start': 100.0, 'end': 130.0, 'confidence': 0.9, 'category': 'sponsor'},
        {'start': 131.0, 'end': 160.0, 'confidence': 0.9, 'category': 'sponsor'},
    ]
    merged = _validator()._merge_close_ads(ads, ValidationResult(ads=[]), actions_map=None)
    assert len(merged) == 1
