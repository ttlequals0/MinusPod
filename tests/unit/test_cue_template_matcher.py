"""Unit tests for the audio cue template matcher (#350)."""
import numpy as np

from audio_analysis.cue_template_matcher import (
    AudioCueTemplateMatcher,
    _peak_pick,
    _sliding_zncc,
)
from audio_analysis.cue_features import (
    N_COEFFS, SAMPLE_RATE_HZ, serialize_mfcc, compute_mfcc, pcm_to_int16_bytes,
)


def _make_template_row(mfcc: np.ndarray, *, template_id: int = 1,
                       label: str = 't', duration_s: float = 0.5):
    return {
        'id': template_id,
        'label': label,
        'mfcc_blob': serialize_mfcc(mfcc),
        'duration_s': duration_s,
        'n_coeffs': mfcc.shape[1],
    }


def test_sliding_zncc_finds_perfect_match():
    rng = np.random.default_rng(42)
    template = rng.standard_normal((20, N_COEFFS)).astype(np.float32)
    haystack = rng.standard_normal((200, N_COEFFS)).astype(np.float32)
    plant_at = 70
    haystack[plant_at:plant_at + 20] = template
    scores = _sliding_zncc(haystack, template)
    assert scores.shape[0] == 200 - 20 + 1
    # Score at the planted index must dominate.
    best = int(np.argmax(scores))
    assert best == plant_at
    assert scores[best] > 0.99


def test_sliding_zncc_handles_too_short_haystack():
    rng = np.random.default_rng(0)
    template = rng.standard_normal((20, N_COEFFS)).astype(np.float32)
    haystack = rng.standard_normal((10, N_COEFFS)).astype(np.float32)
    scores = _sliding_zncc(haystack, template)
    assert scores.size == 0


def test_peak_pick_suppresses_neighbors():
    scores = np.zeros(50, dtype=np.float32)
    scores[10] = 0.9
    scores[11] = 0.88
    scores[30] = 0.92
    peaks = _peak_pick(scores, 0.85, suppress_frames=5)
    assert sorted(p[0] for p in peaks) == [10, 30]


def test_matcher_skips_invalid_templates():
    rng = np.random.default_rng(1)
    good = rng.standard_normal((10, N_COEFFS)).astype(np.float32)
    too_short = rng.standard_normal((1, N_COEFFS)).astype(np.float32)
    bad_blob = {
        'id': 2, 'label': 'bad',
        'mfcc_blob': b'\x00\x01\x02',  # not divisible by n_coeffs
        'duration_s': 1.0, 'n_coeffs': N_COEFFS,
    }
    matcher = AudioCueTemplateMatcher(templates=[
        _make_template_row(good, template_id=1, label='ok'),
        _make_template_row(too_short, template_id=3, label='short'),
        bad_blob,
    ])
    # Only the "ok" template survives validation.
    assert matcher.is_usable
    assert len(matcher._templates) == 1
    assert matcher._templates[0].label == 'ok'


# --- Per-template voiceover-robust profile (#350 4B) ----------------------------

def _tone(freq_hz, seconds=0.6):
    t = np.arange(int(SAMPLE_RATE_HZ * seconds)) / SAMPLE_RATE_HZ
    return (0.7 * np.sin(2 * np.pi * freq_hz * t)).astype(np.float32)


def _row_with_pcm(pcm, *, template_id=1, formant_atten_db=None, with_pcm=True):
    mfcc = compute_mfcc(pcm)   # stored blob computed at 0 dB, like production
    row = {
        'id': template_id, 'label': 'wsj',
        'mfcc_blob': serialize_mfcc(mfcc),
        'duration_s': len(pcm) / SAMPLE_RATE_HZ,
        'n_coeffs': mfcc.shape[1],
        'pcm_blob': pcm_to_int16_bytes(pcm) if with_pcm else None,
        'formant_atten_db': formant_atten_db,
    }
    return row, mfcc


def test_per_template_attenuation_rederives_from_pcm():
    # An AM in-band tone has in-band temporal structure, so a non-zero profile
    # must yield a different (re-derived) MFCC than the stored 0 dB blob.
    t = np.arange(int(SAMPLE_RATE_HZ * 0.6)) / SAMPLE_RATE_HZ
    pcm = ((0.5 + 0.5 * np.sin(2 * np.pi * 4 * t)) * np.sin(2 * np.pi * 1500 * t)).astype(np.float32)
    row, stored = _row_with_pcm(pcm, formant_atten_db=12.0)
    m = AudioCueTemplateMatcher(templates=[row])
    assert m.is_usable
    assert m._templates[0].formant_atten_db == 12.0
    assert not np.array_equal(m._templates[0].mfcc, stored)   # re-derived under profile


def test_global_profile_applies_when_column_null():
    pcm = _tone(1500.0)
    row, stored = _row_with_pcm(pcm, formant_atten_db=None)
    # Column NULL -> inherit the global profile passed to the matcher.
    m = AudioCueTemplateMatcher(templates=[row], formant_atten_db=12.0)
    assert m._templates[0].formant_atten_db == 12.0
    assert not np.array_equal(m._templates[0].mfcc, stored)
    # Default (global 0) -> uses the stored blob unchanged.
    m0 = AudioCueTemplateMatcher(templates=[_row_with_pcm(pcm)[0]])
    assert np.array_equal(m0._templates[0].mfcc, stored)


def test_attenuation_without_pcm_falls_back_to_blob():
    pcm = _tone(1500.0)
    row, stored = _row_with_pcm(pcm, formant_atten_db=12.0, with_pcm=False)
    m = AudioCueTemplateMatcher(templates=[row])
    assert m.is_usable
    # No PCM to re-derive from -> keep the stored blob, reset effective atten to 0.
    assert m._templates[0].formant_atten_db == 0.0
    assert np.array_equal(m._templates[0].mfcc, stored)
