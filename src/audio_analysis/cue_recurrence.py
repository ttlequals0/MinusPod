"""Cluster an episode's loud bursts by acoustic similarity so only RECURRING
sounds are suggested as cue-template candidates (#350 follow-up).

A real ad-break ding repeats across an episode; a laugh or a music hit is a
one-off. The spectral detector flags dozens of loud ~1s bursts per episode, so
grouping them by MFCC similarity and keeping only the clusters that recur cuts
the noise down to the few sounds actually worth templating.
"""
from typing import Dict, List

from audio_analysis.cue_template_matcher import _sliding_zncc

DEFAULT_SIMILARITY = 0.75
DEFAULT_MIN_COUNT = 3


def _similarity(a, b) -> float:
    """Peak sliding-ZNCC between two MFCC matrices (order-independent)."""
    needle, hay = (a, b) if a.shape[0] <= b.shape[0] else (b, a)
    scores = _sliding_zncc(hay, needle)
    return float(scores.max()) if scores.size else 0.0


def cluster_recurring(spots: List[Dict],
                      similarity: float = DEFAULT_SIMILARITY,
                      min_count: int = DEFAULT_MIN_COUNT) -> List[Dict]:
    """Return one representative per recurring-sound cluster.

    Each spot carries an ``'mfcc'`` ndarray plus ``'start'``, ``'end'`` and
    ``'prominenceDb'``. Greedy: seed from the most prominent unassigned spot,
    absorb every unassigned spot whose peak sliding-ZNCC to the seed is
    ``>= similarity``. Keep clusters with ``>= min_count`` members. The
    representative is the seed; ``count`` is the cluster size. Sorted by count
    then prominence, most-recurring first.
    """
    order = sorted(
        range(len(spots)),
        key=lambda i: spots[i].get('prominenceDb') or 0.0,
        reverse=True,
    )
    assigned = [False] * len(spots)
    clusters = []
    for i in order:
        if assigned[i]:
            continue
        assigned[i] = True
        count = 1
        for j in order:
            if assigned[j]:
                continue
            if _similarity(spots[i]['mfcc'], spots[j]['mfcc']) >= similarity:
                assigned[j] = True
                count += 1
        if count >= min_count:
            rep = spots[i]
            clusters.append({
                'start': rep['start'],
                'end': rep['end'],
                'prominenceDb': rep.get('prominenceDb'),
                'count': count,
            })
    clusters.sort(key=lambda c: (c['count'], c.get('prominenceDb') or 0.0), reverse=True)
    return clusters
