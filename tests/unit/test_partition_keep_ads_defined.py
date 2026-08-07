"""tests/unit/test_partition_keep_ads_defined.py"""
from tests.app_bootstrap import bootstrap
bootstrap('partition_keep_test_')

from main_app.processing import _partition_keep_ads


def _marker(**kw):
    m = {'start': 10.0, 'end': 40.0, 'confidence': 1.0, 'category': 'cross_promo'}
    m.update(kw)
    return m


def test_defined_pattern_marker_bypasses_keep():
    keep, remove = _partition_keep_ads(
        [_marker(pattern_defined=True)], {'cross_promo': 'keep'})
    assert keep == []
    assert len(remove) == 1


def test_undefined_marker_still_kept():
    keep, remove = _partition_keep_ads(
        [_marker()], {'cross_promo': 'keep'})
    assert len(keep) == 1
    assert remove == []


def test_defined_marker_notes_overridden_keep():
    keep, remove = _partition_keep_ads(
        [_marker(pattern_defined=True)], {'cross_promo': 'keep'})
    assert remove[0].get('keep_overridden_by_pattern') is True
