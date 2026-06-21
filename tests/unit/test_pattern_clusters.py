"""Unit tests for per-sponsor merge-suggestion clustering + cache (#399)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

import pattern_clusters
from pattern_clusters import merge_suggestions, tiebreaker_key


def _p(pid, sponsor_id, text, conf=0, fp=0, sponsor='Acme'):
    return {
        'id': pid,
        'sponsor_id': sponsor_id,
        'sponsor': sponsor,
        'text_template': text,
        'confirmation_count': conf,
        'false_positive_count': fp,
    }


_READ_A = 'Acme makes great widgets for busy people. Visit acme dot com slash deal for a discount.'
_READ_A2 = 'Acme makes great widgets for busy people. Visit acme dot com slash deal for a discount today.'
_READ_FAR = 'Completely different sponsor copy about unrelated kitchen knives and cookware sets entirely.'


def setup_function(_):
    pattern_clusters._CACHE.clear()


def test_tiebreaker_prefers_confirmations_then_fp_then_length_then_id():
    a = _p(5, 1, 'short', conf=0, fp=0)
    b = _p(3, 1, 'a much longer template than the other one here', conf=0, fp=0)
    # All-zero quality: longer template wins (then lowest id).
    assert sorted([a, b], key=tiebreaker_key)[0]['id'] == 3
    c = _p(9, 1, 'short', conf=5, fp=0)
    # Confirmations dominate length.
    assert sorted([a, b, c], key=tiebreaker_key)[0]['id'] == 9


def test_clusters_two_similar_same_sponsor_patterns():
    out = merge_suggestions([_p(1, 1, _READ_A), _p(2, 1, _READ_A2)])
    assert len(out) == 1
    assert set(out[0]['pattern_ids']) == {1, 2}
    assert out[0]['suggested_keep_id'] in (1, 2)


def test_keep_target_is_tiebreaker_winner():
    # id 2 has more confirmations -> it is the suggested keep target.
    out = merge_suggestions([_p(1, 1, _READ_A, conf=0), _p(2, 1, _READ_A2, conf=3)])
    assert out[0]['suggested_keep_id'] == 2


def test_below_threshold_not_clustered():
    out = merge_suggestions([_p(1, 1, _READ_A), _p(2, 1, _READ_FAR)])
    assert out == []


def test_different_sponsors_never_clustered():
    # Identical text but different sponsor_id -> no cross-sponsor cluster.
    out = merge_suggestions([_p(1, 1, _READ_A), _p(2, 2, _READ_A, sponsor='Other')])
    assert out == []


def test_patterns_without_sponsor_skipped():
    out = merge_suggestions([_p(1, None, _READ_A), _p(2, None, _READ_A2)])
    assert out == []


def test_cache_invalidates_on_text_change():
    g = [_p(1, 1, _READ_A), _p(2, 1, _READ_FAR)]
    assert merge_suggestions(g) == []  # not similar -> no cluster, cached
    # Edit pattern 2 to match pattern 1: signature changes, cache recomputes.
    g[1]['text_template'] = _READ_A2
    out = merge_suggestions(g)
    assert len(out) == 1 and set(out[0]['pattern_ids']) == {1, 2}
