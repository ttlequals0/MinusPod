"""Music/speech discriminator for recurring cue candidates (issue #350).

A common spoken phrase repeats within an episode like an ad-break sting, so the fingerprint
recurrence scan suggests it. It reads as speech: its energy sits in the formant band, it is not
tonal, and it is gappy (pauses between words). A produced sting, even a bass jingle with a
voiceover like a measured content-transition cue (band ratio 0.32, flatness 0.0003, sustained
0.90), is tonal and/or sustained with energy outside the band.

A recurring candidate is dropped only when all three speech-like conditions hold, which keeps
musical cues. Applies to within-episode recurring candidates only; cross-episode intro/outro
candidates (often legitimately spoken) are exempt.

numpy + scipy.fft only (no librosa). Decode spans with ``cue_features.decode_pcm_window``.
"""
import logging

import numpy as np
from scipy.fft import rfft, rfftfreq

from config import (
    AUDIO_CUE_SPEECH_BAND_LO_HZ, AUDIO_CUE_SPEECH_BAND_HI_HZ,
    AUDIO_CUE_SPEECH_BAND_RATIO_MAX, AUDIO_CUE_SPEECH_FLATNESS_MIN,
    AUDIO_CUE_SPEECH_SUSTAINED_MAX,
)

logger = logging.getLogger('podcast.audio_analysis.cue_speech_filter')

_N_FFT = 512
_HOP = 160          # 10 ms at 16 kHz
_SUSTAIN_FLOOR = 0.15   # frame RMS (normalized to the clip max) counted as "energy present"
_WINDOW = np.hanning(_N_FFT).astype(np.float32)


def speechiness_features(pcm, sample_rate=16000, *,
                         lo_hz=AUDIO_CUE_SPEECH_BAND_LO_HZ,
                         hi_hz=AUDIO_CUE_SPEECH_BAND_HI_HZ):
    """Return ``(speech_band_ratio, spectral_flatness, sustained_frac)`` for a clip.

    - speech_band_ratio: share of spectral energy in ``[lo_hz, hi_hz]`` (formants).
    - spectral_flatness: geometric/arithmetic mean of the power spectrum, averaged over frames
      (near 0 = tonal/musical, toward 1 = noisy/speech-like).
    - sustained_frac: fraction of frames whose energy clears a floor (music is continuous, speech gappy).

    Degenerate (too short / silent) clips return music-like values so they are never dropped.
    """
    pcm = np.asarray(pcm, dtype=np.float32)
    if pcm.size < _N_FFT * 2:
        return 0.0, 0.0, 1.0
    # Strided frames (matches cue_features.compute_mfcc) -- avoids a per-frame
    # Python list. The guard above guarantees at least one frame.
    n_frames = 1 + (pcm.size - _N_FFT) // _HOP
    idx = np.arange(_N_FFT)[None, :] + _HOP * np.arange(n_frames)[:, None]
    frames = pcm[idx] * _WINDOW
    spec = rfft(frames, axis=1)
    spectra = spec.real ** 2 + spec.imag ** 2   # power, no redundant abs() sqrt
    total = spectra.sum() + 1e-12
    freqs = rfftfreq(_N_FFT, 1.0 / sample_rate)
    band = (freqs >= lo_hz) & (freqs <= hi_hz)
    speech_band_ratio = float(spectra[:, band].sum() / total)

    ps = spectra + 1e-12
    flatness = float((np.exp(np.log(ps).mean(axis=1)) / ps.mean(axis=1)).mean())

    rms = np.sqrt((frames ** 2).mean(axis=1))
    peak = rms.max() + 1e-12
    sustained_frac = float((rms / peak > _SUSTAIN_FLOOR).mean())
    return speech_band_ratio, flatness, sustained_frac


def is_likely_speech(pcm, sample_rate=16000, *,
                     lo_hz=AUDIO_CUE_SPEECH_BAND_LO_HZ, hi_hz=AUDIO_CUE_SPEECH_BAND_HI_HZ,
                     ratio_max=AUDIO_CUE_SPEECH_BAND_RATIO_MAX,
                     flatness_min=AUDIO_CUE_SPEECH_FLATNESS_MIN,
                     sustained_max=AUDIO_CUE_SPEECH_SUSTAINED_MAX):
    """True only when a clip looks like plain speech on all three axes.

    AND of the three conditions, so a tonal or sustained sting, or one with energy outside the formant band, is kept.
    """
    ratio, flatness, sustained = speechiness_features(
        pcm, sample_rate, lo_hz=lo_hz, hi_hz=hi_hz)
    return ratio > ratio_max and flatness > flatness_min and sustained < sustained_max
