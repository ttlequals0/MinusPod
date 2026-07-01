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

import json
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

# Speech-formant band defaults for the optional voiceover-robust matching (#350):
# attenuating the mel bands centered in [FORMANT_LO_HZ, FORMANT_HI_HZ] down-weights
# a varying voiceover so a cue matches on its constant music bed. Off by default.
FORMANT_LO_HZ = 800.0
FORMANT_HI_HZ = 3400.0

# Mel filterbank cache keyed by (sample_rate, n_fft, n_mels).
_MEL_FILTERBANK_CACHE: dict = {}
# Formant-band weight-vector cache keyed by (sample_rate, n_fft, n_mels, lo, hi, atten_db).
_FORMANT_WEIGHT_CACHE: dict = {}


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


def _formant_band_weights(sample_rate: int, n_fft: int, n_mels: int,
                          lo_hz: float, hi_hz: float, atten_db: float) -> np.ndarray:
    """Per-mel-band multiplicative weights that attenuate the ``[lo_hz, hi_hz]`` band.

    Returns shape ``(n_mels,)``: 1.0 for bands centered outside the band,
    ``10**(-atten_db/20)`` inside, with a raised-cosine ramp (half-octave) on each
    edge so there is no hard notch. ``atten_db <= 0`` returns an all-ones identity
    vector so callers can short-circuit. Only bands whose CENTER falls in the band
    are touched, so sub-band beds and high stings are untouched at any depth.
    """
    if not atten_db or atten_db <= 0:
        return np.ones(n_mels, dtype=np.float32)
    key = (sample_rate, n_fft, n_mels, round(lo_hz, 3), round(hi_hz, 3), round(atten_db, 3))
    cached = _FORMANT_WEIGHT_CACHE.get(key)
    if cached is not None:
        return cached

    low_mel = _hz_to_mel(np.array([0.0]))[0]
    high_mel = _hz_to_mel(np.array([sample_rate / 2.0]))[0]
    centers = _mel_to_hz(np.linspace(low_mel, high_mel, n_mels + 2))[1:n_mels + 1]
    atten = 10.0 ** (-atten_db / 20.0)
    ramp = np.sqrt(2.0)   # half-octave transition on each edge
    w = np.ones(n_mels, dtype=np.float32)
    for i, c in enumerate(centers):
        if lo_hz <= c <= hi_hz:
            w[i] = atten
        elif lo_hz / ramp <= c < lo_hz:
            t = (c - lo_hz / ramp) / (lo_hz - lo_hz / ramp)
            w[i] = 1.0 + (atten - 1.0) * (0.5 - 0.5 * np.cos(np.pi * t))
        elif hi_hz < c <= hi_hz * ramp:
            t = (c - hi_hz) / (hi_hz * ramp - hi_hz)
            w[i] = atten + (1.0 - atten) * (0.5 - 0.5 * np.cos(np.pi * t))
    w = w.astype(np.float32)
    _FORMANT_WEIGHT_CACHE[key] = w
    return w


def compute_mfcc(samples: np.ndarray, sample_rate: int = SAMPLE_RATE_HZ,
                 n_coeffs: int = N_COEFFS, formant_atten_db: float = 0.0,
                 formant_lo_hz: float = FORMANT_LO_HZ,
                 formant_hi_hz: float = FORMANT_HI_HZ) -> np.ndarray:
    """Compute MFCC matrix for a mono float32 PCM array in [-1, 1].

    Returns shape ``(n_frames, n_coeffs)`` float32. Returns an empty
    ``(0, n_coeffs)`` array when the input is too short for even one frame.

    ``formant_atten_db > 0`` multiplicatively down-weights the log-mel bands
    centered in ``[formant_lo_hz, formant_hi_hz]`` before the DCT, reducing the
    speech-formant band's influence on the (zero-meaned) ZNCC match so a cue keys
    on its constant music bed (#350). The default ``0.0`` is a no-op, leaving the
    output byte-identical to the un-weighted MFCC.
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

    # Optional voiceover-robust weighting (#350): down-weight the formant band in
    # the log domain (ZNCC zero-means each coefficient, so a linear/additive scale
    # would cancel -- only a multiplicative log-mel weight reduces a band's
    # influence). No-op when formant_atten_db <= 0.
    if formant_atten_db and formant_atten_db > 0:
        weights = _formant_band_weights(
            sample_rate, n_fft, N_MELS, formant_lo_hz, formant_hi_hz, formant_atten_db)
        log_mel = log_mel * weights[None, :]

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

    raw = _run_ffmpeg_pipe(cmd, op_desc='ffmpeg decode')
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


def _run_ffmpeg_pipe(cmd, input_bytes: Optional[bytes] = None,
                     op_desc: str = 'ffmpeg', timeout: float = FFT_TIMEOUT_S) -> bytes:
    """Run an ffmpeg/ffprobe subprocess and return its stdout bytes.

    ``input_bytes`` is fed on stdin when given (else the command reads a file or
    needs no input). Raises ``RuntimeError`` on timeout or non-zero exit.
    """
    try:
        proc = tracked_run(cmd, input=input_bytes, capture_output=True,
                           timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"{op_desc} timed out") from e
    if proc.returncode != 0:
        stderr = (proc.stderr or b'').decode('utf-8', errors='replace')[:300]
        raise RuntimeError(f"{op_desc} exit {proc.returncode}: {stderr}")
    return proc.stdout or b''


def _probe_audio_stream(data: bytes):
    """Return ``(sample_rate, channels)`` of the first audio stream via ffprobe.

    Used to reject a mismatched stream BEFORE decoding it, so a crafted
    high-sample-rate / multi-channel file cannot be expanded into an enormous
    in-memory PCM buffer first.
    """
    cmd = [
        'ffprobe', '-v', 'error', '-select_streams', 'a:0',
        '-show_entries', 'stream=sample_rate,channels', '-of', 'json', 'pipe:0',
    ]
    out = _run_ffmpeg_pipe(cmd, data, op_desc='ffprobe')
    try:
        streams = (json.loads(out or b'{}') or {}).get('streams') or []
    except (ValueError, TypeError) as e:
        raise RuntimeError(f"could not read audio metadata: {e}") from e
    if not streams:
        raise RuntimeError("no audio stream found")
    return int(streams[0].get('sample_rate') or 0), int(streams[0].get('channels') or 0)


def pcm_to_flac(pcm_bytes: bytes, sample_rate: int) -> bytes:
    """Encode int16 mono PCM to a FLAC byte stream (lossless, ~half the size)."""
    cmd = [
        'ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostdin',
        '-f', 's16le', '-ar', str(sample_rate), '-ac', '1', '-i', 'pipe:0',
        '-c:a', 'flac', '-f', 'flac', 'pipe:1',
    ]
    return _run_ffmpeg_pipe(cmd, bytes(pcm_bytes), op_desc='FLAC encode')


def flac_to_wav(flac_bytes: bytes, max_seconds: float,
                sample_rate: int = SAMPLE_RATE_HZ) -> bytes:
    """Decode a FLAC stream to a 16-bit PCM WAV at its source rate/channels.

    Rejects any stream that is not mono ``sample_rate`` BEFORE decoding (no
    resampling or downmixing), so a crafted high-rate / multi-channel FLAC
    cannot blow up into a multi-GB in-memory WAV. ``max_seconds`` additionally
    bounds the decoded duration as a zip-bomb guard against a long silent FLAC.
    """
    sr, channels = _probe_audio_stream(flac_bytes)
    if sr != sample_rate or channels != 1:
        raise RuntimeError(
            f"cue audio must be mono {sample_rate} Hz, got {channels}ch {sr}Hz")
    cmd = [
        'ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostdin',
        '-i', 'pipe:0', '-t', str(max_seconds),
        '-c:a', 'pcm_s16le', '-f', 'wav', 'pipe:1',
    ]
    return _run_ffmpeg_pipe(cmd, flac_bytes, op_desc='FLAC decode')
