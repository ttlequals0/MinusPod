"""Unit tests for the audio-cue (ding/stinger) detector (issue #350).

The ffmpeg/ebur128 measurement is exercised end-to-end only in the container;
here we drive the pure burst-detection logic with synthetic per-frame
band-loudness measurements so the thresholding, duration gating, and confidence
gating are covered without audio.
"""
from audio_analysis.cue_detector import AudioCueDetector
from audio_analysis.base import SignalType


def _frames(baseline, spikes):
    """Build [(t, loudness)] at 0.1s cadence; spikes maps frame-index -> loudness."""
    meas = [(round(i * 0.1, 1), baseline) for i in range(60)]
    for idx, val in spikes.items():
        meas[idx] = (round(idx * 0.1, 1), val)
    return meas


def test_strong_burst_is_detected():
    d = AudioCueDetector(prominence_db=9.0, min_confidence=0.80)
    # 15 dB above baseline across 4 frames (~0.4s).
    meas = _frames(-45.0, {20: -30.0, 21: -30.0, 22: -30.0, 23: -30.0})
    sigs = d._find_bursts(meas, -45.0)
    assert len(sigs) == 1
    s = sigs[0]
    assert s.signal_type == SignalType.AUDIO_CUE.value
    # First above-threshold frame is at 2.0s; the reported start is pulled
    # back by the ebur128 momentary-loudness onset lag.
    assert s.start == 1.8
    assert s.confidence >= 0.80
    assert s.details['prominence_db'] == 15.0
    assert s.details['band_hz'] == [1500, 8000]


def test_onset_lag_clamps_at_zero():
    # A burst right at the head of the file cannot report a negative start.
    d = AudioCueDetector(prominence_db=9.0, min_confidence=0.80)
    meas = _frames(-45.0, {0: -30.0, 1: -30.0, 2: -30.0, 3: -30.0})
    sigs = d._find_bursts(meas, -45.0)
    assert len(sigs) == 1
    assert sigs[0].start == 0.0


def test_onset_lag_does_not_change_duration_gates():
    # A 1.9s burst passes the 2.0s max-duration gate; the lag widens only the
    # reported span, not the gated measurement.
    d = AudioCueDetector(prominence_db=9.0, min_confidence=0.80, max_duration=2.0)
    spikes = {i: -28.0 for i in range(10, 28)}  # 1.0s..2.7s -> 1.9s measured
    meas = _frames(-45.0, spikes)
    sigs = d._find_bursts(meas, -45.0)
    assert len(sigs) == 1
    assert sigs[0].start == 0.8


def test_weak_burst_dropped_by_confidence_gate():
    # 11 dB clears the 9 dB prominence threshold but confidence (~0.7) is below
    # the 0.80 gate, so nothing is emitted.
    d = AudioCueDetector(prominence_db=9.0, min_confidence=0.80)
    meas = _frames(-45.0, {30: -34.0, 31: -34.0})
    assert d._find_bursts(meas, -45.0) == []


def test_below_threshold_is_not_a_burst():
    # 6 dB never exceeds the 9 dB prominence threshold.
    d = AudioCueDetector(prominence_db=9.0, min_confidence=0.80)
    meas = _frames(-45.0, {10: -39.0, 11: -39.0})
    assert d._find_bursts(meas, -45.0) == []


def test_overlong_burst_is_rejected():
    # A sustained loud stretch (3s) is content/music, not a ding.
    d = AudioCueDetector(prominence_db=9.0, min_confidence=0.80, max_duration=2.0)
    spikes = {i: -28.0 for i in range(10, 40)}  # 10..39 -> ~3s
    meas = _frames(-45.0, spikes)
    assert d._find_bursts(meas, -45.0) == []


def test_lower_confidence_gate_admits_weaker_cue():
    # Same 11 dB burst is admitted once the gate is lowered.
    d = AudioCueDetector(prominence_db=9.0, min_confidence=0.65)
    meas = _frames(-45.0, {30: -34.0, 31: -34.0})
    sigs = d._find_bursts(meas, -45.0)
    assert len(sigs) == 1
    assert sigs[0].confidence >= 0.65


def test_detect_missing_file_returns_empty():
    d = AudioCueDetector()
    assert d.detect('/nonexistent/path/to/audio.mp3') == []
