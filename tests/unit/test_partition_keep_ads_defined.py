"""tests/unit/test_partition_keep_ads_defined.py"""
from tests.app_bootstrap import bootstrap
bootstrap('partition_keep_test_')

from main_app.processing import _partition_keep_ads, _apply_late_keep_safety_net


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


def test_safety_net_defined_pattern_stays_in_cut_list():
    ads_to_remove = [_marker(pattern_defined=True)]
    result = _apply_late_keep_safety_net(
        ads_to_remove, ads_to_remove, {'cross_promo': 'keep'})
    assert len(result) == 1
    assert result[0].get('keep_overridden_by_pattern') is True
    assert result[0].get('was_cut') is None
    assert result[0].get('action_applied') is None


def test_safety_net_undefined_marker_kept():
    ads_to_remove = [_marker()]
    result = _apply_late_keep_safety_net(
        ads_to_remove, ads_to_remove, {'cross_promo': 'keep'})
    assert len(result) == 0
