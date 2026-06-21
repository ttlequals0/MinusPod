"""Unit tests for the generous scan profile (release-threshold hysteresis) on
AudioCueDetector (cue-candidate suggestions, #350 follow-up).

These drive _find_bursts directly with synthetic per-frame loudness so no ffmpeg
or audio file is needed. baseline is passed as 0.0, so each measurement's
loudness IS its prominence over baseline.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from audio_analysis.cue_detector import AudioCueDetector, _FRAME_STEP_SECONDS


def _frames(prominences):
    """[(ts, loudness)] at 0.1s steps; loudness == prominence (baseline 0)."""
    return [(round(i * _FRAME_STEP_SECONDS, 4), p) for i, p in enumerate(prominences)]


# A sustained sting: rises through the release floor, peaks over the detect
# threshold, decays back. Release=3, detect=6.
_SWELL = [1, 2, 4, 7, 9, 8, 5, 4, 2, 1]


def test_hysteresis_captures_more_envelope_than_single_threshold():
    measurements = _frames(_SWELL)

    live = AudioCueDetector(prominence_db=6.0, min_confidence=0.0)  # release_db=None
    scan = AudioCueDetector(prominence_db=6.0, min_confidence=0.0,
                            release_db=3.0, max_duration=12.0)

    live_sig = live._find_bursts(measurements, baseline=0.0)
    scan_sig = scan._find_bursts(measurements, baseline=0.0)

    assert len(live_sig) == 1 and len(scan_sig) == 1
    live_dur = live_sig[0].end - live_sig[0].start
    scan_dur = scan_sig[0].end - scan_sig[0].start
    # The scan window starts no later and ends no earlier, and is strictly
    # longer -- it keeps the attack/decay the single-threshold path clips off.
    assert scan_sig[0].start <= live_sig[0].start
    assert scan_sig[0].end >= live_sig[0].end
    assert scan_dur > live_dur


def test_hysteresis_allows_long_sustained_sound():
    # ~5s continuously above the detect threshold. The live profile (max 2.0s)
    # rejects it; the scan profile (max 12s) keeps it as one candidate.
    measurements = _frames([8] * 50)  # 50 frames * 0.1s = ~5s

    live = AudioCueDetector(prominence_db=6.0, min_confidence=0.0, max_duration=2.0)
    scan = AudioCueDetector(prominence_db=6.0, min_confidence=0.0,
                            release_db=3.0, max_duration=12.0)

    assert live._find_bursts(measurements, baseline=0.0) == []
    scan_sig = scan._find_bursts(measurements, baseline=0.0)
    assert len(scan_sig) == 1
    assert (scan_sig[0].end - scan_sig[0].start) > 2.0


def test_hysteresis_ignores_runs_that_never_cross_detect_threshold():
    # Above the release floor (3) but never above the detect threshold (6):
    # mild content, not a sting. Must not be emitted.
    measurements = _frames([4, 5, 4, 5, 4, 5])
    scan = AudioCueDetector(prominence_db=6.0, min_confidence=0.0, release_db=3.0)
    assert scan._find_bursts(measurements, baseline=0.0) == []


def test_single_threshold_path_unchanged_without_release():
    # Without release_db the detector keeps the original behavior: the window is
    # only the frames above the detect threshold (plus the fixed end step).
    measurements = _frames(_SWELL)
    live = AudioCueDetector(prominence_db=6.0, min_confidence=0.0)
    sig = live._find_bursts(measurements, baseline=0.0)
    assert len(sig) == 1
    # Above-detect frames are ts 0.3, 0.4, 0.5; end = last + one frame step.
    # start is pulled back by the onset lag (0.2s).
    assert sig[0].end == round(0.5 + _FRAME_STEP_SECONDS, 2)
