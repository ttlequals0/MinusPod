"""Near-miss telemetry for the cue template matcher (#350 Phase 6).

The matcher, when given a ``near_miss_floor``, records sub-threshold peaks in
[floor, threshold) as advisory near-misses -- never signals. floor=None keeps
the exact pre-Phase-6 behavior (no near-misses, identical signal set).
"""
import numpy as np

from audio_analysis import cue_template_matcher as ctm
from audio_analysis.cue_template_matcher import AudioCueTemplateMatcher
from audio_analysis.cue_features import N_COEFFS, serialize_mfcc


def _template_row(mfcc, *, template_id=1, label='t', duration_s=0.5):
    return {
        'id': template_id, 'label': label,
        'mfcc_blob': serialize_mfcc(mfcc),
        'duration_s': duration_s, 'n_coeffs': mfcc.shape[1],
    }


def _planted_chunk(rng, template, *, planted):
    """Build a haystack MFCC with events planted at given (frame, weight) pairs.

    weight=1.0 is a perfect copy (score ~1.0); a lower weight mixed with noise
    lands the ZNCC score in a sub-threshold band.
    """
    hay = rng.standard_normal((400, template.shape[1])).astype(np.float32)
    n = template.shape[0]
    for frame, weight in planted:
        noise = rng.standard_normal((n, template.shape[1])).astype(np.float32)
        hay[frame:frame + n] = (weight * template
                                + (1.0 - weight) * 1.3 * noise).astype(np.float32)
    return hay


def _matcher(near_miss_floor=None, threshold=0.85):
    rng = np.random.default_rng(11)
    template = rng.standard_normal((20, N_COEFFS)).astype(np.float32)
    m = AudioCueTemplateMatcher(
        templates=[_template_row(template)],
        score_threshold=threshold,
        near_miss_floor=near_miss_floor,
    )
    return m, template


def test_floor_none_produces_no_near_misses():
    m, template = _matcher(near_miss_floor=None)
    rng = np.random.default_rng(29)
    # One perfect (>= threshold) and one degraded (below threshold) event.
    chunk = _planted_chunk(rng, template, planted=[(50, 1.0), (200, 0.6)])
    matches = {1: []}
    peaks = {1: 0.0}
    misses = {1: []}
    m._scan_chunk(chunk, 0.0, matches, peaks, misses)
    # floor=None: the degraded event is never recorded, only the strong signal.
    assert len(matches[1]) == 1
    assert misses[1] == []


def test_floor_set_partitions_signal_and_near_miss():
    m, template = _matcher(near_miss_floor=0.5)
    rng = np.random.default_rng(29)
    chunk = _planted_chunk(rng, template, planted=[(50, 1.0), (200, 0.6)])
    matches = {1: []}
    peaks = {1: 0.0}
    misses = {1: []}
    m._scan_chunk(chunk, 0.0, matches, peaks, misses)
    # Strong event >= threshold -> signal; degraded event in [floor, threshold)
    # -> near-miss, not a signal.
    assert len(matches[1]) == 1
    assert matches[1][0].confidence >= 0.85
    assert len(misses[1]) == 1
    nm = misses[1][0]
    assert 0.5 <= nm['score'] < 0.85
    assert nm['template_id'] == 1


def test_below_floor_peak_is_dropped():
    # Threshold 0.85, floor 0.80 -> a 0.6 peak is below the floor and recorded
    # neither as a signal nor a near-miss.
    m, template = _matcher(near_miss_floor=0.80)
    rng = np.random.default_rng(29)
    chunk = _planted_chunk(rng, template, planted=[(200, 0.6)])
    matches = {1: []}
    peaks = {1: 0.0}
    misses = {1: []}
    m._scan_chunk(chunk, 0.0, matches, peaks, misses)
    assert matches[1] == []
    assert misses[1] == []


def test_detect_with_debug_dedupes_and_caps_near_misses(monkeypatch):
    m, template = _matcher(near_miss_floor=0.5)
    rng = np.random.default_rng(29)
    # Two degraded events far apart, plus a strong signal. dedup keeps both
    # misses (far apart) and the signal is not a miss.
    chunk = _planted_chunk(rng, template, planted=[(50, 1.0), (150, 0.6), (300, 0.6)])

    monkeypatch.setattr(ctm, 'get_audio_duration', lambda *a, **k: 5.0)
    monkeypatch.setattr(ctm, 'decode_pcm_window',
                        lambda *a, **k: np.zeros(16000, dtype=np.float32))
    monkeypatch.setattr(ctm, 'compute_mfcc', lambda *a, **k: chunk)

    signals, debug = m.detect_with_debug('x.mp3')
    assert 'near_misses' in debug
    assert len(signals) == 1                         # only the strong event
    assert len(debug['near_misses']) == 2            # both degraded events
    for nm in debug['near_misses']:
        assert nm['score'] < 0.85


def test_detect_with_debug_floor_none_has_empty_near_misses(monkeypatch):
    m, template = _matcher(near_miss_floor=None)
    rng = np.random.default_rng(29)
    chunk = _planted_chunk(rng, template, planted=[(50, 1.0), (150, 0.6)])
    monkeypatch.setattr(ctm, 'get_audio_duration', lambda *a, **k: 5.0)
    monkeypatch.setattr(ctm, 'decode_pcm_window',
                        lambda *a, **k: np.zeros(16000, dtype=np.float32))
    monkeypatch.setattr(ctm, 'compute_mfcc', lambda *a, **k: chunk)
    signals, debug = m.detect_with_debug('x.mp3')
    assert debug['near_misses'] == []
    assert len(signals) == 1
