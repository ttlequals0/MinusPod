"""Unit tests for cue_features MFCC extractor (#350)."""
import numpy as np
import pytest

from audio_analysis.cue_features import (
    N_COEFFS,
    N_MELS,
    SAMPLE_RATE_HZ,
    FORMANT_LO_HZ,
    FORMANT_HI_HZ,
    compute_mfcc,
    deserialize_mfcc,
    int16_bytes_to_pcm,
    pcm_to_int16_bytes,
    serialize_mfcc,
    _formant_band_weights,
    _mel_to_hz,
    _hz_to_mel,
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


# --- Voiceover-robust formant weighting (#350 4B) -------------------------------

_N_FFT = 512  # rfft size compute_mfcc uses for a 400-sample (25ms@16k) frame


def _mel_centers(n_mels=N_MELS, sample_rate=SAMPLE_RATE_HZ):
    low = _hz_to_mel(np.array([0.0]))[0]
    high = _hz_to_mel(np.array([sample_rate / 2.0]))[0]
    return _mel_to_hz(np.linspace(low, high, n_mels + 2))[1:n_mels + 1]


def test_formant_weights_only_touch_the_band():
    w = _formant_band_weights(SAMPLE_RATE_HZ, _N_FFT, N_MELS,
                              FORMANT_LO_HZ, FORMANT_HI_HZ, atten_db=12.0)
    centers = _mel_centers()
    expected = 10 ** (-12.0 / 20.0)
    for c, wi in zip(centers, w):
        if FORMANT_LO_HZ <= c <= FORMANT_HI_HZ:
            assert wi == pytest.approx(expected, rel=1e-5)   # attenuated in-band
        elif c < FORMANT_LO_HZ / np.sqrt(2) or c > FORMANT_HI_HZ * np.sqrt(2):
            assert wi == pytest.approx(1.0)                  # untouched well outside


def test_formant_weights_identity_when_off():
    w = _formant_band_weights(SAMPLE_RATE_HZ, _N_FFT, N_MELS,
                              FORMANT_LO_HZ, FORMANT_HI_HZ, atten_db=0.0)
    assert np.array_equal(w, np.ones(N_MELS, dtype=np.float32))


def test_compute_mfcc_atten_zero_is_byte_identical():
    sig = _tone(1500.0, 0.6)
    assert np.array_equal(compute_mfcc(sig), compute_mfcc(sig, formant_atten_db=0.0))


def test_compute_mfcc_attenuation_changes_in_band_signal():
    # An amplitude-modulated in-band tone has real in-band temporal structure, so
    # attenuating that band changes its MFCC.
    t = np.arange(int(SAMPLE_RATE_HZ * 0.6)) / SAMPLE_RATE_HZ
    sig = ((0.5 + 0.5 * np.sin(2 * np.pi * 4 * t)) * np.sin(2 * np.pi * 1500 * t)).astype(np.float32)
    base = compute_mfcc(sig)
    atten = compute_mfcc(sig, formant_atten_db=12.0)
    assert base.shape == atten.shape
    assert not np.array_equal(base, atten)
