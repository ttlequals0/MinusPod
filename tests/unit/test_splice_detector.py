"""SpliceDetector unit tests (docs/specs/2026-07-08-ad-splice-detection.md#21-new-module-srcaudio_analysissplice_detectorpy).

Generates synthetic wavs with ffmpeg (deterministic aevalsrc amplitudes):
a 440 Hz tone, an inserted digital/deep silence, then a louder 880 Hz tone.
Asserts event detection at known offsets within 0.2s. Skipped when ffmpeg
is not on PATH (mirrors tests/integration/test_cue_matcher_real_audio.py).
"""
import os
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from audio_analysis.base import LoudnessFrame
from audio_analysis.splice_detector import SpliceDetector

pytestmark = pytest.mark.skipif(
    shutil.which('ffmpeg') is None, reason='ffmpeg not available')

# aevalsrc gives exact amplitude control (ffmpeg sine amplitude is not documented).
_TONE_A = 'aevalsrc=0.1*sin(2*PI*440*t):s=8000:d=30'        # RMS ~ -23 dBFS
_TONE_B = 'aevalsrc=0.5*sin(2*PI*880*t):s=8000:d=29'        # RMS ~ -9 dBFS
_DIGITAL_GAP = 'aevalsrc=0:s=8000:d=1.0'                     # true zeros, 1.0s
_DEEP_GAP = 'aevalsrc=0.0003*sin(2*PI*440*t):s=8000:d=1.6'   # RMS ~ -73.5 dBFS
_SHORT_GAP = 'aevalsrc=0:s=8000:d=0.3'                       # too short for any event


def _make_wav(path, gap_expr):
    cmd = [
        'ffmpeg', '-y', '-v', 'error',
        '-f', 'lavfi', '-i', _TONE_A,
        '-f', 'lavfi', '-i', gap_expr,
        '-f', 'lavfi', '-i', _TONE_B,
        '-filter_complex', '[0:a][1:a][2:a]concat=n=3:v=0:a=1[out]',
        '-map', '[out]', '-c:a', 'pcm_s16le', path,
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def _loudness_frames(step_at=30.0, before=-23.0, after=-9.0, total=60.0):
    """Synthetic 5s ebur128 frames: `before` LUFS up to step_at, `after` past it."""
    frames = []
    t = 0.0
    while t < total:
        mid = t + 2.5
        frames.append(LoudnessFrame(
            start=t, end=t + 5.0,
            loudness_lufs=(before if mid < step_at else after)))
        t += 5.0
    return frames


def test_digital_silence_detected_at_known_offset(tmp_path):
    wav = str(tmp_path / 'digital.wav')
    _make_wav(wav, _DIGITAL_GAP)
    payload = SpliceDetector().detect(wav, 60.0, _loudness_frames())
    assert payload['version'] == 1
    digital = [e for e in payload['events'] if e['type'] == 'digital_silence']
    assert len(digital) == 1
    ev = digital[0]
    assert abs(ev['time'] - 30.0) <= 0.2
    assert abs(ev['duration_s'] - 1.0) <= 0.2
    assert ev['depth_dbfs'] < -80.0


def test_loudness_step_annotated_and_emitted(tmp_path):
    wav = str(tmp_path / 'digital.wav')
    _make_wav(wav, _DIGITAL_GAP)
    payload = SpliceDetector().detect(wav, 60.0, _loudness_frames())
    ev = next(e for e in payload['events'] if e['type'] == 'digital_silence')
    # Synthetic frames: -23 LUFS before, -9 LUFS after -> +14.0 LU step.
    assert ev['loudness_step_lu'] == pytest.approx(14.0, abs=0.01)
    steps = [e for e in payload['events'] if e['type'] == 'loudness_step']
    assert len(steps) == 1
    assert abs(steps[0]['time'] - 30.0) <= 0.2


def test_spectral_step_across_tone_change(tmp_path):
    wav = str(tmp_path / 'digital.wav')
    _make_wav(wav, _DIGITAL_GAP)
    payload = SpliceDetector().detect(wav, 60.0, _loudness_frames())
    ev = next(e for e in payload['events'] if e['type'] == 'digital_silence')
    # 440 Hz -> 880 Hz: centroid step ~ +440 Hz.
    assert 300.0 <= ev['centroid_step_hz'] <= 600.0
    steps = [e for e in payload['events'] if e['type'] == 'spectral_step']
    assert len(steps) == 1
    assert steps[0]['time'] == pytest.approx(30.0, abs=0.2)


def test_deep_silence_detected(tmp_path):
    wav = str(tmp_path / 'deep.wav')
    _make_wav(wav, _DEEP_GAP)
    payload = SpliceDetector().detect(wav, 60.6, _loudness_frames(total=60.6))
    deep = [e for e in payload['events'] if e['type'] == 'deep_silence']
    assert len(deep) == 1
    ev = deep[0]
    assert abs(ev['time'] - 30.0) <= 0.2
    assert abs(ev['duration_s'] - 1.6) <= 0.2
    assert -80.0 <= ev['depth_dbfs'] < -70.0
    # -73.5 dBFS is above the -80 digital threshold: no digital event.
    assert not any(e['type'] == 'digital_silence' for e in payload['events'])


def test_short_gap_yields_no_events(tmp_path):
    wav = str(tmp_path / 'short.wav')
    _make_wav(wav, _SHORT_GAP)
    payload = SpliceDetector().detect(wav, 59.3, _loudness_frames(total=59.3))
    assert payload['events'] == []


def test_missing_file_returns_empty_payload():
    payload = SpliceDetector().detect('/nonexistent/episode.mp3', 60.0, [])
    assert payload == {'version': 1, 'events': [],
                       'calibration': {'status': 'cold_start'}}
