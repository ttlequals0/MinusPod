"""End-to-end cue template detection on synthetic audio (#350).

Builds a 30 s WAV with two repetitions of a short chirp planted at known times,
marks the first chirp as a template, then runs the template matcher against the
file and asserts both repetitions are found near the planted times. This is the
"verify with unit tests only" gate for the matcher: it proves localization on
real ffmpeg-decoded audio without any labelled production data.

Requires ffmpeg on PATH (the matcher decodes the file via ffmpeg). Skipped if
unavailable.
"""
import shutil
import wave

import numpy as np
import pytest

from audio_analysis.cue_features import (
    SAMPLE_RATE_HZ, compute_mfcc, decode_pcm_window, serialize_mfcc,
)
from audio_analysis.cue_template_matcher import AudioCueTemplateMatcher


def _chirp(duration_s: float, sample_rate: int = SAMPLE_RATE_HZ) -> np.ndarray:
    """Tonal chirp 3-5 kHz, half-sinusoidal amplitude envelope."""
    t = np.arange(int(sample_rate * duration_s)) / sample_rate
    freq = 3000 + (5000 - 3000) * (t / max(duration_s, 1e-9))
    phase = 2 * np.pi * np.cumsum(freq) / sample_rate
    env = np.sin(np.pi * t / duration_s) ** 2
    return (0.6 * env * np.sin(phase)).astype(np.float32)


def _write_wav(path: str, samples: np.ndarray, sample_rate: int = SAMPLE_RATE_HZ) -> None:
    pcm = np.clip(samples, -1.0, 1.0)
    pcm_int16 = (pcm * 32767).astype('<i2')
    with wave.open(path, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_int16.tobytes())


@pytest.fixture
def synthetic_audio(tmp_path):
    if shutil.which('ffmpeg') is None:
        pytest.skip("ffmpeg not available; integration test skipped")
    total_seconds = 30.0
    sample_rate = SAMPLE_RATE_HZ
    background = (
        0.02 * np.random.default_rng(0).standard_normal(
            int(sample_rate * total_seconds),
        ).astype(np.float32)
    )
    chirp = _chirp(0.6, sample_rate)
    plant_times = [5.0, 18.5]
    for t_s in plant_times:
        start = int(t_s * sample_rate)
        end = start + len(chirp)
        background[start:end] = chirp + 0.01 * background[start:end]
    path = str(tmp_path / "cue.wav")
    _write_wav(path, background, sample_rate)
    return path, plant_times


def test_template_matcher_finds_both_chirps(synthetic_audio):
    path, plant_times = synthetic_audio
    # Build a template directly from the first planted chirp using the same
    # extractor the API would use.
    pcm = decode_pcm_window(path, plant_times[0], plant_times[0] + 0.6)
    mfcc = compute_mfcc(pcm)
    assert mfcc.shape[0] > 0
    template_row = {
        'id': 1,
        'label': 'chirp',
        'mfcc_blob': serialize_mfcc(mfcc),
        'duration_s': 0.6,
        'n_coeffs': mfcc.shape[1],
    }
    matcher = AudioCueTemplateMatcher(
        templates=[template_row], score_threshold=0.75,
    )
    signals = matcher.detect(path)
    assert signals, "expected at least one cue signal"
    starts = sorted(s.start for s in signals)
    # Both planted chirps should be found within 0.3 s of their plant times.
    for planted in plant_times:
        assert any(abs(s - planted) < 0.3 for s in starts), (
            f"no detection within 0.3s of planted chirp at {planted}s; got {starts}"
        )
