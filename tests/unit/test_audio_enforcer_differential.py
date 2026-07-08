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


def test_overlapping_window_gets_confirmed_line():
    out = AudioEnforcer().format_for_window(_result_with_diff(), 90.0, 150.0)
    assert 'CONFIRMED dynamically inserted ad at 100.0s-160.0s' in out
    assert 'differs across independent fetches' in out
    assert 'AUDIO SIGNALS' in out


def test_non_overlapping_window_no_line():
    out = AudioEnforcer().format_for_window(_result_with_diff(), 200.0, 260.0)
    assert 'CONFIRMED dynamically inserted' not in out


def test_identical_regions_never_render():
    r = AudioAnalysisResult()
    r.dai_differential = {'status': 'ok', 'regions': [
        {'start_s': 0.0, 'end_s': 100.0, 'kind': 'identical', 'corr': 0.99}]}
    out = AudioEnforcer().format_for_window(r, 0.0, 60.0)
    assert out == ''


def test_no_dai_attribute_behaves_as_before():
    out = AudioEnforcer().format_for_window(AudioAnalysisResult(), 0.0, 60.0)
    assert out == ''
