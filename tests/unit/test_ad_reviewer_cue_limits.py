import pytest

from ad_reviewer import _cue_snap_limits


def _snapped_ad(edge, remove_with_ad):
    return {'cue_snap': {edge: {
        'source': 'template', 'template_id': 1, 'cue_start': 2521.19, 'cue_end': 2522.615,
        'original': 2521.26, 'shift_seconds': 1.405, 'remove_with_ad': remove_with_ad}}}


def test_removed_cue_floors_end_at_cue_end():
    _, ends = _cue_snap_limits(_snapped_ad('end', True), 2460.2, 2522.665)
    assert ends == [pytest.approx((2522.665, 2522.665))]


def test_kept_cue_floors_end_before_cue_start():
    _, ends = _cue_snap_limits(_snapped_ad('end', False), 2460.2, 2521.14)
    assert ends == [pytest.approx((2521.14, 2521.14))]


def test_removed_cue_floors_start_before_cue_start():
    starts, _ = _cue_snap_limits(_snapped_ad('start', True), 2521.14, 2600.0)
    assert starts == [pytest.approx((2521.14, 2521.14))]


def test_kept_cue_floors_start_after_cue_end():
    starts, _ = _cue_snap_limits(_snapped_ad('start', False), 2522.665, 2600.0)
    assert starts == [pytest.approx((2522.665, 2522.665))]


def test_missing_remove_flag_keeps_the_kept_cue_behaviour():
    ad = _snapped_ad('end', True)
    del ad['cue_snap']['end']['remove_with_ad']
    _, ends = _cue_snap_limits(ad, 2460.2, 2521.14)
    assert ends == [pytest.approx((2521.14, 2521.14))]
