"""Unit tests for the audio cue template matcher (#350)."""
import numpy as np

from audio_analysis.cue_template_matcher import (
    AudioCueTemplateMatcher,
    _peak_pick,
    _sliding_zncc,
)
from audio_analysis.cue_features import N_COEFFS, serialize_mfcc


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
