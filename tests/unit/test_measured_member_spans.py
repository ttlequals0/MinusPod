import os
import sys
import tempfile

os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='measured_test_'))
os.environ.setdefault('SECRET_KEY', 'test-secret')
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from ad_detector import AdDetector
from utils.markers import (
    COVERAGE_GAP_TOLERANCE,
    EDGE_TOLERANCE,
    clip_member_spans,
    measured_member_spans,
    note_merged_members,
    recorded_member_spans,
    union_cover,
)


def _ad(start, end, stage, **extra):
    return {'start': start, 'end': end, 'detection_stage': stage, **extra}


def test_tolerance_constants():
    assert EDGE_TOLERANCE == 0.05
    assert COVERAGE_GAP_TOLERANCE == 3.0


def test_member_records_precise_end_before_invalidation():
    detector = AdDetector(api_key='test-key')
    quoted = _ad(100.0, 150.0, 'claude', confidence=0.95, sponsor='Acme',
                 quote_aligned_end=True, quote_end=150.0,
                 quote_original_end=160.0)
    following = _ad(151.0, 200.0, 'claude', confidence=0.9, sponsor='Acme')

    merged = detector._merge_detection_results([quoted, following])[0]

    assert merged['end'] == 200.0
    assert 'quote_aligned_end' not in merged
    first, second = recorded_member_spans(merged)
    assert first['start'] == 100.0 and first['end'] == 150.0
    assert first['precise_end'] is True
    assert first['precise_start'] is False
    assert first['confidence'] == 0.95
    assert second['precise_end'] is False


def test_word_timed_edge_counts_as_precise():
    target = _ad(10.0, 40.0, 'claude', confidence=0.9, word_timed_start=10.0)
    note_merged_members(target, _ad(45.0, 60.0, 'claude', confidence=0.9))
    member = recorded_member_spans(target)[0]
    assert member['precise_start'] is True
    assert member['precise_end'] is False


def test_coalesced_member_keeps_widened_edge_flags():
    target = _ad(0.0, 50.0, 'claude', confidence=0.7,
                 quote_aligned_start=True, quote_start=0.0)
    other = _ad(40.0, 80.0, 'claude', confidence=0.92,
                quote_aligned_end=True, quote_end=80.0)
    note_merged_members(target, other)
    (member,) = recorded_member_spans(target)
    assert (member['start'], member['end']) == (0.0, 80.0)
    assert member['confidence'] == 0.7
    assert member['precise_start'] is True
    assert member['precise_end'] is True

    target = _ad(0.0, 50.0, 'claude', confidence=0.9,
                 quote_aligned_end=True, quote_end=50.0)
    note_merged_members(target, _ad(40.0, 80.0, 'claude', confidence=0.8))
    (member,) = recorded_member_spans(target)
    assert member['precise_end'] is False


def test_fingerprint_member_records_match_span():
    fp = _ad(0.0, 175.0, 'fingerprint', confidence=0.9,
             fingerprint_match_start=0.0, fingerprint_match_end=60.0)
    note_merged_members(fp, _ad(176.0, 190.0, 'claude', confidence=0.9))
    member = recorded_member_spans(fp)[0]
    assert member['fingerprint_match_start'] == 0.0
    assert member['fingerprint_match_end'] == 60.0


def test_clip_clears_precise_flag_on_a_moved_edge():
    target = _ad(0.0, 50.0, 'claude', confidence=0.9,
                 quote_aligned_start=True, quote_start=0.0,
                 quote_aligned_end=True, quote_end=50.0)
    note_merged_members(target, _ad(60.0, 80.0, 'claude', confidence=0.9))
    clip_member_spans(target, 0.0, 40.0)
    (member,) = recorded_member_spans(target)
    assert member['precise_start'] is True
    assert member['precise_end'] is False
    assert member['confidence'] == 0.9


def test_union_cover_gap_tolerance():
    assert union_cover([(0.0, 10.0), (10.36, 20.0)], 0.0, 20.0) == (0.0, 20.0)
    assert union_cover([(0.0, 10.0), (15.0, 20.0)], 0.0, 20.0) == (0.0, 10.0)
    assert union_cover([(0.04, 10.0)], 0.0, 20.0) == (0.0, 10.0)
    assert union_cover([(0.0, 19.96)], 0.0, 20.0) == (0.0, 20.0)
    assert union_cover([(0.06, 10.0)], 0.0, 20.0) == (0.06, 10.0)


def test_union_cover_clips_and_handles_no_spans():
    assert union_cover([(-5.0, 30.0)], 0.0, 20.0) == (0.0, 20.0)
    assert union_cover([], 0.0, 20.0) == (None, None)
    assert union_cover([(25.0, 30.0)], 0.0, 20.0) == (None, None)
    # No span reaches the start edge: the leftmost covered run is returned.
    assert union_cover([(8.0, 12.0), (13.0, 15.0)], 0.0, 20.0) == (8.0, 15.0)


def _estimated_tail_marker():
    marker = _ad(0.0, 60.0, 'claude', confidence=0.96)
    note_merged_members(marker, _ad(60.4, 175.0, 'text_pattern',
                                    span_estimated=True, text_start=60.4,
                                    text_end=89.0))
    marker['end'] = 175.0
    return marker


def test_measured_member_spans_excludes_estimated_tail():
    marker = _estimated_tail_marker()
    assert measured_member_spans(marker, 0.8) == [(0.0, 60.0), (60.4, 89.0)]

    note_merged_members(marker, _ad(100.0, 170.0, 'claude', confidence=0.6))
    assert measured_member_spans(marker, 0.8) == [(0.0, 60.0), (60.4, 89.0)]

    fp = _ad(0.0, 175.0, 'fingerprint', confidence=0.9,
             fingerprint_match_start=0.0, fingerprint_match_end=60.0)
    note_merged_members(fp, _ad(176.0, 190.0, 'claude', confidence=0.5))
    assert measured_member_spans(fp, 0.8) == [(0.0, 60.0)]


def test_anchors_exclude_dai_core_and_auto_estimates():
    marker = _estimated_tail_marker()
    marker['dai_core_spans'] = [{'start': 150.0, 'end': 170.0}]
    assert measured_member_spans(marker, 0.8, anchors_only=True) == [(0.0, 60.0)]

    defined = _ad(0.0, 30.0, 'claude', confidence=0.5)
    note_merged_members(defined, _ad(30.0, 90.0, 'text_pattern',
                                     span_estimated=True, pattern_defined=True,
                                     text_start=30.0, text_end=50.0))
    assert measured_member_spans(defined, 0.8, anchors_only=True) == [(30.0, 50.0)]


def test_estimate_without_text_bounds_contributes_nothing():
    marker = _ad(0.0, 60.0, 'claude', confidence=0.96)
    note_merged_members(marker, _ad(60.0, 175.0, 'text_pattern',
                                    span_estimated=True))
    marker['end'] = 175.0
    assert measured_member_spans(marker, 0.8) == [(0.0, 60.0)]


def test_measured_member_spans_evidence_kinds():
    marker = _ad(0.0, 10.0, 'cue_pair')
    for other in (_ad(12.0, 20.0, 'manual'),
                  _ad(22.0, 30.0, 'keep_content', confidence=0.99),
                  _ad(32.0, 40.0, 'dai_differential'),
                  _ad(42.0, 50.0, 'claude', confidence=0.8)):
        note_merged_members(marker, other)
    marker['dai_core_spans'] = [{'start': 32.0, 'end': 38.0}]
    assert measured_member_spans(marker, 0.8) == [
        (0.0, 10.0), (12.0, 20.0), (32.0, 38.0), (42.0, 50.0)]


def test_measured_member_spans_on_an_unmerged_marker():
    assert measured_member_spans(_ad(5.0, 25.0, 'claude', confidence=0.9),
                                 0.8) == [(5.0, 25.0)]
    assert measured_member_spans(_ad(5.0, 25.0, 'claude', confidence=0.5),
                                 0.8) == []


def test_coalesced_coarse_member_keeps_the_weakest_confidence():
    marker = _ad(0.0, 60.0, 'claude', confidence=0.96)
    note_merged_members(marker, _ad(50.0, 175.0, 'claude', confidence=0.6))
    (member,) = recorded_member_spans(marker)
    assert member['confidence'] == 0.6
    assert measured_member_spans(marker, 0.8) == []

    marker = _ad(0.0, 60.0, 'claude', confidence=0.96)
    note_merged_members(marker, _ad(50.0, 175.0, 'claude', confidence=0.85))
    assert measured_member_spans(marker, 0.8) == [(0.0, 175.0)]
