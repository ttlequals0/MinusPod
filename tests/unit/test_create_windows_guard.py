"""Regression test for the create_windows infinite-loop guard (config-1)."""
from ad_detector.prompts import create_windows


def _segments(n=30, span=10.0):
    return [{'start': i * span, 'end': i * span + span, 'text': 'x'} for i in range(n)]


def test_overlap_ge_window_size_terminates():
    # overlap >= window_size makes step_size <= 0; the loop must still
    # terminate (non-overlapping fallback) instead of hanging the worker.
    windows = create_windows(_segments(), window_size=120, overlap=1770)
    assert windows  # produced something
    assert len(windows) < 1000  # bounded, not runaway


def test_overlap_equal_window_size_terminates():
    windows = create_windows(_segments(), window_size=600, overlap=600)
    assert isinstance(windows, list)


def test_normal_overlap_unchanged():
    windows = create_windows(_segments(), window_size=600, overlap=180)
    assert isinstance(windows, list)
