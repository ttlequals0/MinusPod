"""Unit tests for recurring-cue clustering (#350 follow-up)."""
import numpy as np

from audio_analysis.cue_recurrence import cluster_recurring


def _mfcc(seed, n=10):
    """Deterministic, varied MFCC so sliding-ZNCC is meaningful."""
    return np.random.RandomState(seed).randn(n, 13).astype(np.float32)


def _spot(start, mfcc, prom=15.0):
    return {'start': start, 'end': start + 0.6, 'prominenceDb': prom, 'mfcc': mfcc}


def test_only_recurring_clusters_are_returned():
    ding = _mfcc(1)
    spots = [
        _spot(10.0, ding, prom=18.0),   # the recurring ding x3
        _spot(120.0, ding, prom=16.0),
        _spot(240.0, ding, prom=17.0),
        _spot(50.0, _mfcc(2)),          # two one-off laughs (different sounds)
        _spot(80.0, _mfcc(3)),
    ]
    out = cluster_recurring(spots, similarity=0.75, min_count=3)
    assert len(out) == 1
    assert out[0]['count'] == 3
    assert out[0]['start'] == 10.0   # representative is the most prominent member


def test_min_count_gates_suggestions():
    pair = _mfcc(5)
    spots = [_spot(10.0, pair), _spot(60.0, pair)]   # recurs only twice
    assert cluster_recurring(spots, similarity=0.75, min_count=3) == []
    assert len(cluster_recurring(spots, similarity=0.75, min_count=2)) == 1


def test_sorted_by_count_desc():
    a, b = _mfcc(7), _mfcc(8)
    spots = (
        [_spot(i * 10.0, a) for i in range(5)]      # recurs 5x
        + [_spot(100.0 + i * 10.0, b) for i in range(3)]  # recurs 3x
    )
    out = cluster_recurring(spots, similarity=0.75, min_count=3)
    assert [c['count'] for c in out] == [5, 3]


def test_empty():
    assert cluster_recurring([], min_count=3) == []
