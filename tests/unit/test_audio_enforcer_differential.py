"""audio_enforcer differential-region prompt lines (Layer 3)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from audio_analysis.base import AudioAnalysisResult
from audio_enforcer import AudioEnforcer

_DIFF = {'status': 'ok', 'regions': [
    {'start_s': 100.0, 'end_s': 160.0, 'kind': 'differential', 'corr': 0.0},
    {'start_s': 0.0, 'end_s': 100.0, 'kind': 'identical', 'corr': 0.99},
]}


def _result_with_diff():
    r = AudioAnalysisResult()
    r.dai_differential = _DIFF
    return r


def test_overlapping_window_gets_middle_ground_hint_line():
    # #541: the differential line is a middle-ground hint -- "LIKELY an ad,
    # flag it when consistent" -- not the old absolute "CONFIRMED ... not part
    # of the show" and not so neutral the model ignores real DAI ads.
    out = AudioEnforcer().format_for_window(_result_with_diff(), 90.0, 150.0)
    assert 'Audio differs across fetches at 100.0s-160.0s' in out
    assert 'LIKELY an ad' in out
    assert 'Flag it as an ad' in out
    assert 'CONFIRMED' not in out
    assert 'not part of the show' not in out
    assert 'AUDIO SIGNALS' in out


def test_non_overlapping_window_no_line():
    out = AudioEnforcer().format_for_window(_result_with_diff(), 200.0, 260.0)
    assert 'Audio differs across fetches' not in out


def test_identical_regions_never_render():
    r = AudioAnalysisResult()
    r.dai_differential = {'status': 'ok', 'regions': [
        {'start_s': 0.0, 'end_s': 100.0, 'kind': 'identical', 'corr': 0.99}]}
    out = AudioEnforcer().format_for_window(r, 0.0, 60.0)
    assert out == ''


def test_no_dai_attribute_behaves_as_before():
    out = AudioEnforcer().format_for_window(AudioAnalysisResult(), 0.0, 60.0)
    assert out == ''
