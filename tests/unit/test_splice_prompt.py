"""Splice-evidence prompt rendering (item d of docs/specs/2026-07-08-ad-splice-detection.md#23-consumers-none-cuts-on-audio-alone).

Detection windows (AudioEnforcer.format_for_window) and reviewer per-ad
prompts (_format_cue_section) must render in-window / near-boundary splice
events and omit out-of-window ones.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from ad_reviewer import _format_cue_section
from audio_analysis.base import AudioAnalysisResult
from audio_enforcer import AudioEnforcer


def _event(t, etype='digital_silence', depth=-92.0, dur=1.0,
           step_lu=None, centroid=None, flatness=None):
    return {'time': t, 'end_time': t + dur, 'type': etype,
            'depth_dbfs': depth, 'duration_s': dur,
            'loudness_step_lu': step_lu, 'centroid_step_hz': centroid,
            'flatness_step': flatness}


def _result(events):
    r = AudioAnalysisResult()
    r.splice_evidence = {'version': 1, 'events': events,
                         'calibration': {'status': 'calibrated'}}
    return r


def test_window_renders_in_window_event_and_omits_out_of_window():
    result = _result([_event(30.0), _event(500.0)])
    out = AudioEnforcer().format_for_window(result, 0.0, 60.0)
    assert 'digital silence at 30.0s-31.0s' in out
    assert '500.0s' not in out
    assert 'SPLICE EVIDENCE' in out
    assert '=== AUDIO SIGNALS ===' in out


def test_window_renders_steps_with_magnitudes():
    result = _result([
        _event(30.0, etype='loudness_step', depth=None, step_lu=8.2),
        _event(30.0, etype='spectral_step', depth=None,
               centroid=440.0, flatness=0.13),
    ])
    out = AudioEnforcer().format_for_window(result, 0.0, 60.0)
    assert '+8.2 LU' in out
    assert '+440 Hz' in out


def test_window_silence_event_missing_depth_omits_none_literal():
    result = _result([_event(30.0, depth=None)])
    out = AudioEnforcer().format_for_window(result, 0.0, 60.0)
    assert 'digital silence at 30.0s-31.0s' in out
    assert 'None' not in out
    assert 'depth unknown' in out


def test_window_without_events_or_signals_is_empty():
    out = AudioEnforcer().format_for_window(_result([]), 0.0, 60.0)
    assert out == ''


def test_reviewer_section_renders_near_boundary_event():
    result = _result([_event(1798.5, depth=-88.0, dur=1.4), _event(300.0)])
    out = _format_cue_section(audio_analysis=result,
                              ad_start=1800.0, ad_end=1890.0)
    assert 'SPLICE EVIDENCE NEAR BOUNDARIES:' in out
    assert 'digital_silence at 1798.5s-1799.9s' in out
    assert 'depth -88.0 dBFS' in out
    assert '300.0s' not in out
    # One guidance sentence accompanies the events.
    assert 'dynamic ad insertion' in out


def test_reviewer_section_empty_without_nearby_events():
    result = _result([_event(300.0)])
    out = _format_cue_section(audio_analysis=result,
                              ad_start=1800.0, ad_end=1890.0)
    assert out == ''
