"""Unit tests for cue_features MFCC extractor (#350)."""
import numpy as np

from audio_analysis.cue_features import (
    N_COEFFS,
    SAMPLE_RATE_HZ,
    compute_mfcc,
    deserialize_mfcc,
    int16_bytes_to_pcm,
    pcm_to_int16_bytes,
    serialize_mfcc,
)


def _tone(freq_hz: float, duration_s: float, sample_rate: int = SAMPLE_RATE_HZ) -> np.ndarray:
    t = np.arange(int(sample_rate * duration_s)) / sample_rate
    return (0.5 * np.sin(2 * np.pi * freq_hz * t)).astype(np.float32)


def test_compute_mfcc_shape_matches_signal_length():
    sig = _tone(440.0, 0.5)
    mfcc = compute_mfcc(sig)
    # 25 ms frame, 10 ms hop @ 16kHz on 500ms ~ 48 frames.
    assert mfcc.shape[1] == N_COEFFS
    assert 40 < mfcc.shape[0] < 55


def test_compute_mfcc_is_deterministic():
    sig = _tone(880.0, 0.3)
    a = compute_mfcc(sig)
    b = compute_mfcc(sig)
    np.testing.assert_array_equal(a, b)


def test_compute_mfcc_changes_with_pitch():
    """A different tone yields a different MFCC matrix."""
    sig_a = _tone(440.0, 0.5)
    sig_b = _tone(2000.0, 0.5)
    a = compute_mfcc(sig_a)
    b = compute_mfcc(sig_b)
    # Truncate to the shorter of the two if framing rounding differs by 1.
    n = min(a.shape[0], b.shape[0])
    assert not np.allclose(a[:n], b[:n], atol=1e-3)


def test_compute_mfcc_empty_for_very_short_signal():
    sig = np.zeros(10, dtype=np.float32)
    mfcc = compute_mfcc(sig)
    assert mfcc.shape == (0, N_COEFFS)


def test_serialize_deserialize_roundtrip():
    rng = np.random.default_rng(0)
    mfcc = rng.standard_normal((20, N_COEFFS)).astype(np.float32)
    blob = serialize_mfcc(mfcc)
    restored = deserialize_mfcc(blob, N_COEFFS)
    np.testing.assert_array_equal(mfcc, restored)


def test_deserialize_rejects_bad_size():
    import pytest
    rng = np.random.default_rng(1)
    blob = serialize_mfcc(rng.standard_normal((10, N_COEFFS)).astype(np.float32))
    with pytest.raises(ValueError):
        deserialize_mfcc(blob, N_COEFFS + 1)


def test_pcm_int16_roundtrip_preserves_signal():
    """Raw PCM source-of-truth survives the int16 round-trip within quantization."""
    sig = _tone(1200.0, 0.4)
    blob = pcm_to_int16_bytes(sig)
    restored = int16_bytes_to_pcm(blob)
    assert restored.shape == sig.shape
    # int16 quantization error is bounded by one LSB (1/32768).
    assert np.abs(sig - restored).max() < 1.0 / 32768.0 + 1e-6
