"""Multi-chunk matcher path + cross-chunk dedupe (#350).

Forces the chunked decode (normally 600 s chunks) onto a short synthetic file by
monkeypatching CHUNK_SECONDS, planting a chirp straddling a chunk boundary, and
asserting it is found exactly once (not duplicated across the overlap).
"""
import shutil
import wave

import numpy as np
import pytest

from audio_analysis import cue_template_matcher as ctm
from audio_analysis.cue_features import (
    SAMPLE_RATE_HZ, compute_mfcc, decode_pcm_window, serialize_mfcc,
)


def _chirp(duration_s, sr=SAMPLE_RATE_HZ):
    t = np.arange(int(sr * duration_s)) / sr
    freq = 3000 + 2000 * (t / max(duration_s, 1e-9))
    phase = 2 * np.pi * np.cumsum(freq) / sr
    env = np.sin(np.pi * t / duration_s) ** 2
    return (0.6 * env * np.sin(phase)).astype(np.float32)


def _write_wav(path, samples, sr=SAMPLE_RATE_HZ):
    pcm = (np.clip(samples, -1, 1) * 32767).astype('<i2')
    with wave.open(path, 'wb') as wf:
        wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(sr)
        wf.writeframes(pcm.tobytes())


def test_chirp_on_chunk_boundary_found_once(tmp_path, monkeypatch):
    if shutil.which('ffmpeg') is None:
        pytest.skip('ffmpeg not available')
    # Force three small chunks with overlap so a boundary-straddling chirp is
    # decoded in two chunks.
    monkeypatch.setattr(ctm, 'CHUNK_SECONDS', 4)
    monkeypatch.setattr(ctm, 'CHUNK_OVERLAP_SECONDS', 1)

    total = 10.0
    bg = 0.02 * np.random.default_rng(0).standard_normal(int(SAMPLE_RATE_HZ * total)).astype(np.float32)
    chirp = _chirp(0.5)
    # Plant near the 4 s chunk boundary so it lands in the overlap of chunk 1/2.
    plant = 3.8
    s = int(plant * SAMPLE_RATE_HZ)
    bg[s:s + len(chirp)] = chirp
    path = str(tmp_path / 'cue.wav')
    _write_wav(path, bg)

    pcm = decode_pcm_window(path, plant, plant + 0.5)
    mfcc = compute_mfcc(pcm)
    matcher = ctm.AudioCueTemplateMatcher(
        templates=[{
            'id': 1, 'label': 'chirp', 'mfcc_blob': serialize_mfcc(mfcc),
            'duration_s': 0.5, 'n_coeffs': mfcc.shape[1],
        }],
        score_threshold=0.75,
    )
    signals = matcher.detect(path)
    near = [s for s in signals if abs(s.start - plant) < 0.4]
    # Exactly one match despite the chirp being decoded in two overlapping chunks.
    assert len(near) == 1, f'expected one boundary match, got {[round(s.start, 2) for s in signals]}'
