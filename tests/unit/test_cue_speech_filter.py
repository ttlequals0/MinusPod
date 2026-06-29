"""Tests for the recurring-cue music/speech discriminator (#350 4A)."""
import numpy as np
from scipy.fft import rfft, irfft, rfftfreq

from audio_analysis.cue_speech_filter import speechiness_features, is_likely_speech

SR = 16000


def _band_limited_gappy_noise(seconds=3.0, lo=300, hi=3400, seed=1):
    """Formant-band noise with speech-like gaps: high band ratio, high flatness,
    low sustained -> the shape a common spoken phrase has."""
    rng = np.random.default_rng(seed)
    n = rng.standard_normal(int(SR * seconds))
    spec = rfft(n)
    freqs = rfftfreq(len(n), 1.0 / SR)
    spec[(freqs < lo) | (freqs > hi)] = 0
    sig = irfft(spec).astype(np.float32)
    sig /= np.abs(sig).max() + 1e-9
    for s in range(0, len(sig), int(SR * 0.5)):   # 0.3s on, 0.2s off
        sig[s + int(SR * 0.3):s + int(SR * 0.5)] = 0
    return sig


def _bass_tone(seconds=3.0, hz=180.0):
    """Sustained low tone: a music-bed-like sting (the WSJ case shape)."""
    t = np.arange(int(SR * seconds)) / SR
    return (0.8 * np.sin(2 * np.pi * hz * t)).astype(np.float32)


def test_speech_like_clip_is_flagged():
    assert is_likely_speech(_band_limited_gappy_noise(), SR) is True


def test_sustained_bass_tone_is_kept():
    # Tonal + sustained + energy outside the formant band -> not speech.
    assert is_likely_speech(_bass_tone(), SR) is False


def test_features_match_expected_shape():
    ratio, flat, sustained = speechiness_features(_bass_tone(), SR, lo_hz=300, hi_hz=3400)
    assert flat < 0.02          # tonal
    assert sustained > 0.65     # continuous
    sratio, sflat, ssust = speechiness_features(_band_limited_gappy_noise(), SR,
                                                lo_hz=300, hi_hz=3400)
    assert sratio > 0.55        # energy concentrated in the formant band
    assert sflat > 0.02         # noisy / non-tonal


def test_degenerate_clip_is_kept():
    # Too short / silent -> never dropped (filter only removes confident speech).
    assert is_likely_speech(np.zeros(100, dtype=np.float32), SR) is False
    assert is_likely_speech(np.zeros(SR, dtype=np.float32), SR) is False
