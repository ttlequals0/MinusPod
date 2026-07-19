"""Pattern-rewrite anchor gate (regression R3b).

_maybe_rewrite_pattern_from_adjustment rewrites a pattern's text_template
from trimmed bounds when the trim clears min_trim_threshold (20s). An
unanchored (mid-segment) trim must NOT propagate cross-episode: the rewrite
only fires when every trimmed boundary lands within BOUNDARY_SNAP_TOLERANCE_S
of a transcript segment edge.
"""
import os
import tempfile
from unittest.mock import MagicMock

os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='anchor_gate_test_'))
os.environ.setdefault('SECRET_KEY', 'test-secret')

from api.patterns import (
    _maybe_rewrite_pattern_from_adjustment,
    _unanchored_trim_bounds,
)

# Segment edges at 100.0, 120.0, 160.0, 200.0 seconds.
TRANSCRIPT = (
    "[00:01:40.000 --> 00:02:00.000] intro of the ad read\n"
    "[00:02:00.000 --> 00:02:40.000] the ad body with sponsor copy\n"
    "[00:02:40.000 --> 00:03:20.000] trailing show content\n"
)


def _db():
    db = MagicMock()
    db.get_setting_bool.return_value = True
    db.get_setting_float.return_value = 20.0
    return db


def _rewrite(adjusted_start, adjusted_end,
             original_start=100.0, original_end=200.0):
    service = MagicMock()
    service.rewrite_pattern_from_bounds.return_value = True
    _maybe_rewrite_pattern_from_adjustment(
        _db(), service, 7, TRANSCRIPT,
        original_start, original_end, adjusted_start, adjusted_end,
    )
    return service


def test_anchored_trim_rewrites_pattern():
    # End trimmed 40s, landing exactly on the 160.0 edge.
    service = _rewrite(100.0, 160.0)
    service.rewrite_pattern_from_bounds.assert_called_once_with(
        7, TRANSCRIPT, 100.0, 200.0, 100.0, 160.0)


def test_near_edge_trim_within_tolerance_rewrites():
    # 158.0 is 2.0s from the 160.0 edge: inside tolerance, still anchored.
    service = _rewrite(100.0, 158.0)
    service.rewrite_pattern_from_bounds.assert_called_once()


def test_mid_segment_trim_skips_rewrite():
    # 170.0 is 10s from the nearest edges (160.0 / 200.0): the exact
    # unanchored 20s+ class that must not propagate cross-episode.
    service = _rewrite(100.0, 170.0)
    service.rewrite_pattern_from_bounds.assert_not_called()


def test_one_unanchored_boundary_blocks_rewrite():
    # End lands on an edge but the moved start (110.0) is 10s from any edge.
    service = _rewrite(110.0, 160.0)
    service.rewrite_pattern_from_bounds.assert_not_called()


def test_unmoved_boundary_needs_no_anchor():
    # Start unchanged (mid-segment original is irrelevant); only the moved
    # end must anchor.
    service = _rewrite(130.0, 160.0, original_start=130.0, original_end=200.0)
    service.rewrite_pattern_from_bounds.assert_called_once()


def test_unanchored_trim_bounds_reports_moved_unanchored_only():
    unanchored = _unanchored_trim_bounds(TRANSCRIPT, 100.0, 200.0, 110.0, 160.0)
    assert unanchored == [('start', 110.0)]
    assert _unanchored_trim_bounds(TRANSCRIPT, 100.0, 200.0, 100.0, 160.0) == []


def test_unanchored_trim_bounds_empty_transcript_blocks_everything():
    assert _unanchored_trim_bounds('', 100.0, 200.0, 100.0, 160.0) == [
        ('end', 160.0)]
