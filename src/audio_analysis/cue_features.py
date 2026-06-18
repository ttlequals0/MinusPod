"""MFCC feature extraction for audio cue templates (#350).

Pure numpy + scipy. No librosa. Used by both template ingest (compute MFCC
from a user-selected episode window) and the per-episode template matcher
(compute MFCC for the whole episode once, then slide).

Audio path:
    ffmpeg -> 16 kHz mono PCM int16
    -> framing 25 ms / 10 ms hop
    -> Hamming window
    -> magnitude spectrum (rfft)
    -> 26-band mel filterbank
    -> log
    -> DCT-II (orthonormal) keep first ``n_coeffs`` coefficients (drop c0)

We drop c0 because it tracks frame energy, which differs between user-marked
windows and matching occurrences after compression/normalization. The first
13 retained coeffs are ``c1..c13`` in DCT order. No cepstral mean
normalization is applied -- see the note in :func:`compute_mfcc`.
"""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Optional

import numpy as np
from scipy.fft import dct, rfft

from utils.subprocess_registry import tracked_run

logger = logging.getLogger('podcast.audio_analysis.cue_features')


SAMPLE_RATE_HZ = 16000
FRAME_LENGTH_MS = 25
FRAME_HOP_MS = 10
N_MELS = 26
N_COEFFS = 13  # excludes c0
PRE_EMPHASIS = 0.97
FFT_TIMEOUT_S = 120

# Mel filterbank cache keyed by (sample_rate, n_fft, n_mels).
_MEL_FILTERBANK_CACHE: dict = {}


def _hz_to_mel(hz: np.ndarray) -> np.ndarray:
    return 2595.0 * np.log10(1.0 + hz / 700.0)


def _mel_to_hz(mel: np.ndarray) -> np.ndarray:
    return 700.0 * (10.0 ** (mel / 2595.0) - 1.0)


def _mel_filterbank(sample_rate: int, n_fft: int, n_mels: int) -> np.ndarray:
    """Triangular mel filterbank, shape ``(n_mels, n_fft // 2 + 1)``."""
    key = (sample_rate, n_fft, n_mels)
    cached = _MEL_FILTERBANK_CACHE.get(key)
    if cached is not None:
        return cached

    low_mel = _hz_to_mel(np.array([0.0]))[0]
    high_mel = _hz_to_mel(np.array([sample_rate / 2.0]))[0]
    mel_points = np.linspace(low_mel, high_mel, n_mels + 2)
    hz_points = _mel_to_hz(mel_points)
    bins = np.floor((n_fft + 1) * hz_points / sample_rate).astype(int)

    n_bins = n_fft // 2 + 1
    fb = np.zeros((n_mels, n_bins), dtype=np.float32)
    for m in range(1, n_mels + 1):
        left, center, right = bins[m - 1], bins[m], bins[m + 1]
        if center == left:
            center = left + 1
        if right == center:
            right = center + 1
        for k in range(left, min(center, n_bins)):
            fb[m - 1, k] = (k - left) / (center - left)
        for k in range(center, min(right, n_bins)):
            fb[m - 1, k] = (right - k) / (right - center)

    _MEL_FILTERBANK_CACHE[key] = fb
    return fb


def compute_mfcc(samples: np.ndarray, sample_rate: int = SAMPLE_RATE_HZ,
                 n_coeffs: int = N_COEFFS) -> np.ndarray:
    """Compute MFCC matrix for a mono float32 PCM array in [-1, 1].

    Returns shape ``(n_frames, n_coeffs)`` float32. Returns an empty
    ``(0, n_coeffs)`` array when the input is too short for even one frame.
    """
    if samples.ndim != 1:
        samples = samples.reshape(-1)
    if samples.dtype != np.float32:
        samples = samples.astype(np.float32)

    if len(samples) < 2:
        return np.zeros((0, n_coeffs), dtype=np.float32)

    # Pre-emphasis: boost high frequencies; standard for MFCC.
    emphasized = np.empty_like(samples)
    emphasized[0] = samples[0]
    emphasized[1:] = samples[1:] - PRE_EMPHASIS * samples[:-1]

    frame_length = int(round(sample_rate * FRAME_LENGTH_MS / 1000))
    frame_hop = int(round(sample_rate * FRAME_HOP_MS / 1000))
    if len(emphasized) < frame_length:
        return np.zeros((0, n_coeffs), dtype=np.float32)

    n_frames = 1 + (len(emphasized) - frame_length) // frame_hop
    # Strided view to avoid copy; copy via .copy() to make it writable for window.
    indices = (
        np.arange(frame_length)[None, :] + frame_hop * np.arange(n_frames)[:, None]
    )
    frames = emphasized[indices].copy()
    window = np.hamming(frame_length).astype(np.float32)
    frames *= window

    # Next power-of-two FFT size for speed.
    n_fft = 1 << (frame_length - 1).bit_length()
    spectrum = np.abs(rfft(frames, n=n_fft, axis=1)).astype(np.float32)
    power = (spectrum ** 2) / float(n_fft)

    fb = _mel_filterbank(sample_rate, n_fft, N_MELS)
    mel_energy = power @ fb.T
    # Floor to avoid log(0).
    mel_energy = np.maximum(mel_energy, 1e-10)
    log_mel = np.log(mel_energy)

    # DCT-II orthonormal; drop c0 (energy), keep next n_coeffs.
    cepstrum = dct(log_mel, type=2, axis=1, norm='ortho')
    mfcc = cepstrum[:, 1:1 + n_coeffs].astype(np.float32)

    # No cepstral mean normalization. CMN sounds attractive (it cancels
    # stationary channel EQ) but applied per-input it normalizes the template
    # against its own short-window mean and the haystack against its
    # long-window mean -- two different baselines -- so even the cue's source
    # episode scores ~0.4 against its own template. The matcher already
    # zero-means each window before correlating, which is magnitude-invariant;
    # channel EQ differences across episodes of the same show are typically
    # small enough that raw MFCCs still score >= 0.8 on a real recurrence.
    return mfcc


def decode_pcm_window(audio_path: Path | str,
                      start_seconds: float = 0.0,
                      end_seconds: Optional[float] = None,
                      sample_rate: int = SAMPLE_RATE_HZ) -> np.ndarray:
    """ffmpeg-decode a window of audio to mono float32 in [-1, 1].

    Raises ``RuntimeError`` on ffmpeg failure or empty output.
    """
    audio_path = Path(audio_path)
    if not audio_path.exists():
        raise RuntimeError(f"audio file not found: {audio_path}")

    start = max(0.0, float(start_seconds))
    cmd = [
        'ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostdin',
        '-ss', f'{start:.3f}',
        '-i', str(audio_path),
    ]
    if end_seconds is not None:
        duration = float(end_seconds) - start
        if duration <= 0:
            raise RuntimeError(f"window end ({end_seconds}) must be > start ({start})")
        cmd += ['-t', f'{duration:.3f}']
    cmd += ['-ac', '1', '-ar', str(sample_rate), '-f', 's16le', 'pipe:1']

    try:
        proc = tracked_run(cmd, capture_output=True, timeout=FFT_TIMEOUT_S)
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"ffmpeg decode timed out after {FFT_TIMEOUT_S}s") from e

    if proc.returncode != 0:
        stderr = (proc.stderr or b'').decode('utf-8', errors='replace')[:500]
        raise RuntimeError(f"ffmpeg exit {proc.returncode}: {stderr}")

    raw = proc.stdout or b''
    if not raw:
        raise RuntimeError("ffmpeg produced no audio data (empty window?)")

    pcm = np.frombuffer(raw, dtype='<i2').astype(np.float32) / 32768.0
    return pcm


def pcm_to_int16_bytes(pcm: np.ndarray) -> bytes:
    """Pack a mono float32 PCM array in [-1, 1] as little-endian int16 bytes.

    This is the raw-PCM source-of-truth stored alongside the derived MFCC so a
    template can be re-derived if the MFCC params ever change, and exported as
    a lossless WAV. Inverse of the ``<i2 / 32768`` decode in
    :func:`decode_pcm_window`.
    """
    clipped = np.clip(pcm, -1.0, 1.0)
    return (clipped * 32767.0).round().astype('<i2').tobytes()


def int16_bytes_to_pcm(blob: bytes) -> np.ndarray:
    """Inverse of :func:`pcm_to_int16_bytes`; returns float32 PCM in [-1, 1]."""
    return np.frombuffer(blob, dtype='<i2').astype(np.float32) / 32768.0


def serialize_mfcc(mfcc: np.ndarray) -> bytes:
    """Pack a float32 MFCC matrix as little-endian bytes for BLOB storage."""
    if mfcc.dtype != np.float32:
        mfcc = mfcc.astype(np.float32)
    return mfcc.astype('<f4').tobytes()


def deserialize_mfcc(blob: bytes, n_coeffs: int) -> np.ndarray:
    """Inverse of ``serialize_mfcc``; restores shape from byte length / n_coeffs."""
    arr = np.frombuffer(blob, dtype='<f4')
    if n_coeffs <= 0:
        raise ValueError(f"n_coeffs must be > 0, got {n_coeffs}")
    if arr.size % n_coeffs:
        raise ValueError(
            f"mfcc blob size {arr.size} not divisible by n_coeffs {n_coeffs}"
        )
    return arr.reshape(-1, n_coeffs).astype(np.float32)
