"""A reviewer trim may not move an edge inward past a removable template cue inside the span."""
from types import SimpleNamespace

import pytest

from tests.app_bootstrap import bootstrap

bootstrap('reviewer_interior_cue_floor_test_')

from ad_detector.cue_boundary_snap import SNAP_GAP_SECONDS
from tests.unit.reviewer_test_utils import _reviewer

START, END = 2464.6, 2534.2


def _cue(start, end, remove=True, role='boundary', confidence=0.986):
    details = {'source': 'template', 'role': role, 'label': 'ad-break boundary',
               'remove_with_ad': remove, 'cue_type': 'boundary'}
    return SimpleNamespace(start=start, end=end, confidence=confidence, details=details)


def _analysis(*cues):
    return SimpleNamespace(get_signals_by_type=lambda kind: list(cues))


def _clamp(proposed_start, proposed_end, *cues):
    ad = {'start': START, 'end': END, 'confidence': 0.95}
    return _reviewer()._clamp_proposed_bounds(
        ad, proposed_start, proposed_end, START, END, 60, 'show', 'ep1',
        audio_analysis=_analysis(*cues))


def test_start_stops_before_an_interior_removable_cue():
    start, end = _clamp(2473.84, END, _cue(2471.48, 2472.905))
    assert start == pytest.approx(2471.48 - SNAP_GAP_SECONDS)
    assert end == END


def test_kept_interior_cue_is_not_a_limit_so_the_edge_lands_after_it():
    assert _clamp(2473.84, END, _cue(2471.48, 2472.905, remove=False)) == (2473.84, END)


@pytest.mark.parametrize('proposed_start, cue', [
    (2473.84, _cue(2400.0, 2401.0)),
    (2468.0, _cue(2471.48, 2472.905)),
    (2473.84, _cue(2471.48, 2472.905, confidence=0.5)),
], ids=['before_original_edge', 'after_proposed_start', 'low_confidence'])
def test_cue_does_not_clamp(proposed_start, cue):
    assert _clamp(proposed_start, END, cue) == (proposed_start, END)


def test_end_stops_after_an_interior_removable_cue():
    start, end = _clamp(START, 2525.0, _cue(2528.0, 2529.4))
    assert start == START
    assert end == pytest.approx(2529.4 + SNAP_GAP_SECONDS)


def test_end_cue_past_the_proposed_end_only():
    assert _clamp(START, 2531.0, _cue(2528.0, 2529.4)) == (START, 2531.0)
