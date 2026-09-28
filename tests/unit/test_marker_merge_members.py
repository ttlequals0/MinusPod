import os
import sys
import tempfile

import pytest

os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='mergemem_test_'))
os.environ.setdefault('SECRET_KEY', 'test-secret')
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from ad_detector import AdDetector
from ad_detector.boundaries import (
    deduplicate_window_ads,
    split_conflicting_action_span,
)
from tests.unit.marker_test_utils import _ad, member_bases
from utils.markers import (
    carve_fragment,
    clip_dai_core_spans,
    clip_merge_spans,
    dai_core_bounds,
    edge_support,
    estimated_text_bounds,
    hard_member_spans,
    mark_distinct_merge,
    merge_dai_core_spans,
    note_merged_members,
    protected_member_spans,
    recorded_member_spans,
)

MIN_CONF = 0.8


def _estimated(start, end, text_start, text_end):
    return _ad(start, end, 'text_pattern', span_estimated=True,
               text_start=text_start, text_end=text_end)


def test_claude_members_are_protected():
    base = _ad(100.0, 130.0, 'claude')
    note_merged_members(base, _ad(131.0, 160.0, 'claude'))
    assert base['merged_protected_start'] == 100.0
    assert base['merged_protected_end'] == 160.0


def test_all_differential_members_yield_null_protection():
    base = _ad(837.2, 1040.0, 'dai_differential')
    note_merged_members(base, _ad(1041.0, 1068.5, 'dai_differential'))
    assert base['merged_protected_start'] is None
    assert base['merged_protected_end'] is None


def test_mixed_members_protect_only_anchored_span():
    base = _ad(830.0, 980.0, 'dai_differential')
    note_merged_members(base, _ad(891.3, 1007.9, 'claude'))
    assert base['merged_protected_start'] == 891.3
    assert base['merged_protected_end'] == 1007.9


def test_chained_merges_do_not_promote_extended_span():
    # claude base absorbs a differential tail, span is extended by the
    # caller, then a second merge folds another differential region. The
    # protected union must stay the original claude span.
    base = _ad(100.0, 130.0, 'claude')
    note_merged_members(base, _ad(131.0, 170.0, 'dai_differential'))
    base['end'] = 170.0
    note_merged_members(base, _ad(171.0, 200.0, 'dai_differential'))
    base['end'] = 200.0
    assert base['merged_protected_start'] == 100.0
    assert base['merged_protected_end'] == 130.0


def test_folding_a_previously_merged_marker_carries_its_protection():
    other = _ad(300.0, 400.0, 'dai_differential')
    other['merged_protected_start'] = 320.0
    other['merged_protected_end'] = 360.0
    base = _ad(100.0, 290.0, 'text_pattern')
    note_merged_members(base, other)
    assert base['merged_protected_start'] == 100.0
    assert base['merged_protected_end'] == 360.0


def test_keep_content_and_cue_pair_members_are_protected():
    base = _ad(100.0, 130.0, 'keep_content')
    note_merged_members(base, _ad(131.0, 160.0, 'cue_pair'))
    assert base['merged_protected_start'] == 100.0
    assert base['merged_protected_end'] == 160.0


def test_unknown_stage_fails_protected():
    base = _ad(100.0, 130.0, 'dai_differential')
    note_merged_members(base, _ad(131.0, 160.0, 'some_future_stage'))
    assert base['merged_protected_start'] == 131.0
    assert base['merged_protected_end'] == 160.0


def test_protected_end_key_absent_does_not_raise():
    # Malformed persisted marker: start key present, end key missing.
    # _protected_bounds must degrade like the reviewer-side consumers do.
    other = _ad(300.0, 400.0, 'dai_differential')
    other['merged_protected_start'] = 320.0
    base = _ad(100.0, 290.0, 'text_pattern')
    note_merged_members(base, other)
    assert base['merged_protected_start'] == 100.0
    assert base['merged_protected_end'] == 290.0


def test_overlap_extension_widens_protected_union():
    # A distinct merge records protection, then a true-overlap detection of
    # the trailing ad extends the span. The extension must join the
    # protected union or a later trim could sever the added tail.
    ads = [
        {'start': 100.0, 'end': 130.0, 'detection_stage': 'claude',
         'confidence': 0.9, 'reason': 'ad one'},
        {'start': 131.0, 'end': 170.0, 'detection_stage': 'claude',
         'confidence': 0.9, 'reason': 'ad two'},
        {'start': 165.0, 'end': 220.0, 'detection_stage': 'claude',
         'confidence': 0.9, 'reason': 'ad two re-detected across windows'},
    ]
    merged = deduplicate_window_ads(ads)
    assert len(merged) == 1
    m = merged[0]
    assert m['end'] == 220.0
    assert m['merged_distinct_ads'] is True
    assert m['merged_protected_start'] == 100.0
    assert m['merged_protected_end'] == 220.0


def test_dai_core_survives_merge_with_coarser_candidate():
    base = _ad(80.0, 170.0, 'claude')
    differential = _ad(100.0, 160.0, 'dai_differential')
    differential['dai_core_spans'] = [{'start': 100.0, 'end': 160.0}]

    merge_dai_core_spans(base, differential)

    assert base['dai_core_spans'] == [{'start': 100.0, 'end': 160.0}]


def test_dai_cores_merge_and_clip_to_split_piece():
    marker = _ad(100.0, 220.0, 'dai_differential')
    marker['dai_core_spans'] = [
        {'start': 100.0, 'end': 150.0},
        {'start': 150.0, 'end': 220.0},
    ]

    clip_dai_core_spans(marker, 140.0, 180.0)

    assert marker['dai_core_spans'] == [
        {'start': 140.0, 'end': 150.0},
        {'start': 150.0, 'end': 180.0},
    ]


def test_non_finite_dai_core_values_are_ignored():
    marker = _ad(100.0, 220.0, 'dai_differential')
    marker['dai_core_spans'] = [
        {'start': 100.0, 'end': float('inf')},
        {'start': float('-inf'), 'end': 150.0},
        {'start': float('nan'), 'end': 160.0},
        {'start': 120.0, 'end': 180.0},
    ]

    assert dai_core_bounds(marker) == (120.0, 180.0)


def test_oversized_dai_core_values_are_ignored():
    marker = _ad(100.0, 220.0, 'dai_differential')
    marker['dai_core_spans'] = [
        {'start': 10 ** 400, 'end': 10 ** 400 + 1},
        {'start': 120.0, 'end': 180.0},
    ]

    assert dai_core_bounds(marker) == (120.0, 180.0)


def test_boolean_dai_core_values_are_ignored():
    marker = _ad(100.0, 220.0, 'dai_differential')
    marker['dai_core_spans'] = [
        {'start': False, 'end': True},
        {'start': 120.0, 'end': 180.0},
    ]

    assert dai_core_bounds(marker) == (120.0, 180.0)


def test_non_list_dai_core_value_is_ignored():
    marker = _ad(100.0, 220.0, 'dai_differential')
    marker['dai_core_spans'] = 1

    assert dai_core_bounds(marker) == (None, None)
    clip_dai_core_spans(marker, 100.0, 220.0)
    assert 'dai_core_spans' not in marker


def test_merge_chain_records_one_span_per_protected_member():
    base = _ad(100.0, 130.0, 'claude')
    note_merged_members(base, _ad(131.0, 160.0, 'text_pattern'))
    base['end'] = 160.0
    note_merged_members(base, _ad(161.0, 200.0, 'claude'))
    base['end'] = 200.0

    assert member_bases(base['merged_member_spans']) == [
        {'start': 100.0, 'end': 130.0, 'stage': 'claude'},
        {'start': 131.0, 'end': 160.0, 'stage': 'text_pattern'},
        {'start': 161.0, 'end': 200.0, 'stage': 'claude'},
    ]
    assert base['merged_protected_start'] == 100.0
    assert base['merged_protected_end'] == 200.0


def test_unprotected_members_contribute_no_span():
    base = _ad(837.2, 1040.0, 'dai_differential')
    note_merged_members(base, _ad(1041.0, 1068.5, 'dai_differential'))
    assert member_bases(base['merged_member_spans']) == []


def test_merged_member_contributes_its_members_not_its_union():
    other = _ad(300.0, 400.0, 'dai_differential')
    other['merged_protected_start'] = 300.0
    other['merged_protected_end'] = 400.0
    other['merged_member_spans'] = [
        {'start': 300.0, 'end': 340.0, 'stage': 'claude'},
        {'start': 360.0, 'end': 400.0, 'stage': 'claude'},
    ]
    base = _ad(100.0, 290.0, 'text_pattern')

    note_merged_members(base, other)

    assert member_bases(base['merged_member_spans']) == [
        {'start': 100.0, 'end': 290.0, 'stage': 'text_pattern'},
        {'start': 300.0, 'end': 340.0, 'stage': 'claude'},
        {'start': 360.0, 'end': 400.0, 'stage': 'claude'},
    ]
    assert base['merged_protected_start'] == 100.0
    assert base['merged_protected_end'] == 400.0


def test_legacy_merged_member_contributes_its_union_as_one_span():
    # Persisted before member tracking: the union is all that is known, and
    # an unknown stage stays measured-hard.
    other = _ad(300.0, 400.0, 'dai_differential')
    other['merged_protected_start'] = 320.0
    other['merged_protected_end'] = 360.0
    base = _ad(100.0, 290.0, 'text_pattern')

    note_merged_members(base, other)

    assert member_bases(base['merged_member_spans']) == [
        {'start': 100.0, 'end': 290.0, 'stage': 'text_pattern'},
        {'start': 320.0, 'end': 360.0, 'stage': None},
    ]


def test_estimated_text_pattern_member_records_only_its_matched_text():
    # No outro matched, so the end is the pattern's average duration, not
    # evidence.
    base = _ad(649.4, 921.1, 'claude')
    other = _estimated(831.75, 999.65, 831.75, 860.0)

    note_merged_members(base, other)

    assert member_bases(base['merged_member_spans']) == [
        {'start': 649.4, 'end': 921.1, 'stage': 'claude'},
        {'start': 831.75, 'end': 860.0, 'stage': 'text_pattern'},
    ]
    # The union follows the recorded members, not the estimated tail.
    assert base['merged_protected_start'] == 649.4
    assert base['merged_protected_end'] == 921.1


def test_text_pattern_member_keeps_full_span():
    base = _ad(649.4, 921.1, 'claude')

    note_merged_members(base, _ad(831.75, 999.65, 'text_pattern',
                                  span_estimated=False, text_start=831.75,
                                  text_end=860.0))

    assert member_bases(base['merged_member_spans'][1]) == {
        'start': 831.75, 'end': 999.65, 'stage': 'text_pattern'}


@pytest.mark.parametrize('other', [
    pytest.param(_estimated(831.75, 999.65, 700.0, 800.0), id='text_before_span'),
    pytest.param(carve_fragment(_estimated(831.75, 999.65, 831.75, 860.0),
                                900.0, 999.65),
                 id='carved_estimated_tail'),
    pytest.param(_ad(831.75, 999.65, 'text_pattern', span_estimated=True),
                 id='no_text_bounds'),
])
def test_estimate_holding_none_of_its_text_contributes_no_member(other):
    base = _ad(649.4, 921.1, 'claude')

    note_merged_members(base, other)

    assert member_bases(base['merged_member_spans']) == [
        {'start': 649.4, 'end': 921.1, 'stage': 'claude'}]
    assert base['merged_protected_end'] == 921.1


def test_estimate_moved_by_a_snap_still_narrows_to_its_text():
    # A snap that moved the end does not turn the estimated tail into evidence.
    base = _ad(649.4, 700.0, 'claude')

    note_merged_members(base, _estimated(831.75, 1000.05, 831.75, 860.0))

    assert member_bases(base['merged_member_spans'][1]) == {
        'start': 831.75, 'end': 860.0, 'stage': 'text_pattern'}


def test_a_promoted_estimate_flag_cannot_narrow_the_accumulator():
    # Stage-priority promotion copies span_estimated and the text bounds onto
    # an accumulator holding another member's audio. The fold already tracked
    # it, so the next merge reads its recorded members, not the flag.
    base = _ad(649.4, 921.1, 'claude')
    note_merged_members(base, _estimated(831.75, 999.65, 831.75, 860.0))
    base['end'] = 999.65
    base['detection_stage'] = 'text_pattern'
    base['span_estimated'] = True
    base['text_start'], base['text_end'] = 831.75, 860.0
    members = [{'start': 649.4, 'end': 921.1, 'stage': 'claude'},
               {'start': 831.75, 'end': 860.0, 'stage': 'text_pattern'}]
    assert member_bases(base['merged_member_spans']) == members

    later = _ad(1100.0, 1200.0, 'claude')
    note_merged_members(later, base)

    assert member_bases(later['merged_member_spans'][1:]) == members


@pytest.mark.parametrize('stage,expected', [
    pytest.param('claude', [{'start': 100.0, 'end': 400.0, 'stage': 'claude'},
                            {'start': 403.0, 'end': 500.0, 'stage': 'claude'}],
                 id='coarse_coalesces'),
    pytest.param('fingerprint',
                 [{'start': 100.0, 'end': 300.0, 'stage': 'fingerprint'},
                  {'start': 150.0, 'end': 400.0, 'stage': 'fingerprint'},
                  {'start': 403.0, 'end': 500.0, 'stage': 'fingerprint'}],
                 id='measured_stays_separate'),
])
def test_overlapping_same_stage_members(stage, expected):
    # Two LLM windows over one ad are one member; two measured detections are
    # two, and each still has to survive a trim.
    base = _ad(100.0, 300.0, stage)
    note_merged_members(base, _ad(150.0, 400.0, stage))
    base['end'] = 400.0
    note_merged_members(base, _ad(403.0, 500.0, stage))

    assert member_bases(base['merged_member_spans']) == expected


def test_overlapping_members_of_different_stages_stay_separate():
    base = _ad(100.0, 300.0, 'claude')

    note_merged_members(base, _ad(150.0, 400.0, 'text_pattern'))

    assert member_bases(base['merged_member_spans']) == [
        {'start': 100.0, 'end': 300.0, 'stage': 'claude'},
        {'start': 150.0, 'end': 400.0, 'stage': 'text_pattern'}]


def test_clip_merge_spans_narrows_the_union_and_its_members():
    # What every hand-narrowing site (split piece, approved trim, boundary
    # adjustment) owes the records the wider span left behind.
    marker = _ad(100.0, 150.0, 'claude')
    mark_distinct_merge(marker, _ad(160.0, 300.0, 'text_pattern'))
    marker['end'] = 300.0

    clip_merge_spans(marker, 100.0, 150.0)

    assert member_bases(marker['merged_member_spans']) == [
        {'start': 100.0, 'end': 150.0, 'stage': 'claude'}]
    assert (marker['merged_protected_start'],
            marker['merged_protected_end']) == (100.0, 150.0)


def test_protected_spans_prefer_recorded_members():
    tracked = {'merged_distinct_ads': True,
               'merged_protected_start': 100.0, 'merged_protected_end': 180.0,
               'merged_member_spans': [{'start': 100.0, 'end': 130.0,
                                        'stage': 'claude'}]}
    assert protected_member_spans(tracked, 90.0, 200.0) == [
        {'start': 100.0, 'end': 130.0, 'stage': 'claude'}]


def test_protected_spans_shape_a_legacy_union_as_one_measured_member():
    legacy = {'merged_distinct_ads': True,
              'merged_protected_start': 100.0, 'merged_protected_end': 180.0}
    assert protected_member_spans(legacy, 90.0, 200.0) == [
        {'start': 100.0, 'end': 180.0, 'stage': None}]


def test_protected_spans_fall_back_to_the_callers_bounds():
    untracked = {'merged_distinct_ads': True}
    assert protected_member_spans(untracked, 90.0, 200.0) == [
        {'start': 90.0, 'end': 200.0, 'stage': None}]


def test_protected_spans_are_empty_when_no_member_was_anchored():
    unprotected = {'merged_distinct_ads': True, 'merged_member_spans': [],
                   'merged_protected_start': None, 'merged_protected_end': None}
    assert protected_member_spans(unprotected, 90.0, 200.0) == []


def test_window_dedup_records_member_spans():
    ads = [
        {'start': 100.0, 'end': 130.0, 'detection_stage': 'claude',
         'confidence': 0.9, 'reason': 'ad one'},
        {'start': 131.0, 'end': 170.0, 'detection_stage': 'claude',
         'confidence': 0.9, 'reason': 'ad two'},
    ]

    merged = deduplicate_window_ads(ads)

    assert member_bases(merged[0]['merged_member_spans']) == [
        {'start': 100.0, 'end': 130.0, 'stage': 'claude'},
        {'start': 131.0, 'end': 170.0, 'stage': 'claude'},
    ]


MERGE_KEYS = ('merged_distinct_ads', 'merged_protected_start',
              'merged_protected_end', 'merged_member_spans')


def _tracked_merge(start, end):
    """Marker carrying full merge bookkeeping across [start, end]."""
    mid = (start + end) / 2
    ad = _ad(start, mid, 'claude')
    mark_distinct_merge(ad, _ad(mid, end, 'claude'))
    ad['end'] = end
    return ad


def _split(last, current, last_action=None, current_action=None):
    return split_conflicting_action_span(
        last, current, last_action, current_action)


def test_split_fragments_drop_stale_merge_bookkeeping():
    # Every path that carves a narrower piece must drop bookkeeping that
    # described the wider span, member spans included.
    plain = _ad(100.0, 150.0, 'claude')

    _, entries = _split(dict(plain), _tracked_merge(120.0, 220.0))
    legacy_clamped = entries[0]

    _, entries = _split(dict(plain), _tracked_merge(120.0, 220.0),
                        'keep', 'remove')
    loser_tail = entries[0]

    before, entries = _split(_tracked_merge(100.0, 220.0),
                             _ad(120.0, 150.0, 'claude'), 'remove', 'remove')
    nested_after = entries[1]

    shortened, _ = _split(_tracked_merge(100.0, 220.0),
                          _ad(200.0, 300.0, 'claude'), 'remove', 'remove')

    for fragment in (legacy_clamped, loser_tail, before, nested_after,
                     shortened):
        for key in MERGE_KEYS:
            assert key not in fragment


def test_recorded_union_widens_but_never_shrinks():
    # A persisted union has no member span backing it, so a later merge may
    # only extend it; replacing it would drop audio an earlier merge protected.
    base = _ad(100.0, 400.0, 'claude')
    base['merged_protected_start'] = 100.0
    base['merged_protected_end'] = 400.0
    base['merged_member_spans'] = [{'start': 100.0, 'end': 180.0,
                                    'stage': 'claude'}]

    note_merged_members(base, _ad(60.0, 70.0, 'claude'))

    assert base['merged_protected_start'] == 60.0
    assert base['merged_protected_end'] == 400.0


def test_estimated_text_bounds_needs_finite_marker_bounds():
    assert estimated_text_bounds(
        {'span_estimated': True, 'text_start': 1.0, 'text_end': 2.0}) is None
    assert estimated_text_bounds(
        {'span_estimated': True, 'text_start': 1.0, 'text_end': 2.0,
         'start': 'unset', 'end': float('nan')}) is None


def test_estimated_text_bounds_never_inverts():
    # Text recorded past the marker's end claims no audio rather than a
    # negative range.
    assert estimated_text_bounds(
        {'span_estimated': True, 'start': 10.0, 'end': 20.0,
         'text_start': 30.0, 'text_end': 40.0}) == (30.0, 30.0)


def _precise_llm(start, end, **extra):
    return _ad(start, end, 'claude', confidence=0.98, sponsor='Acme', category='sponsor',
               word_timed_start=start, word_timed_end=end, **extra)


def _fingerprint(start, end):
    return _ad(start, end, 'fingerprint', confidence=0.95, sponsor='Acme', category='sponsor',
               fingerprint_match_start=start, fingerprint_match_end=end)


def _envelope():
    detector = AdDetector.__new__(AdDetector)
    (merged,) = detector._merge_detection_results(
        [_precise_llm(1711.02, 1836.66), _fingerprint(1770.0, 1872.74)])
    return merged


def test_edge_support_prefers_precise_transcript_end_over_fingerprint_envelope():
    merged = _envelope()

    assert merged['end'] == 1872.74
    assert edge_support(merged, 'end', MIN_CONF) == {
        'envelope': 1872.74, 'measured': 1836.66, 'source': 'transcript', 'precise': True}
    assert edge_support(merged, 'start', MIN_CONF) == {
        'envelope': 1711.02, 'measured': 1711.02, 'source': 'transcript', 'precise': True}


def test_segment_end_text_pattern_is_soft_past_a_precise_word_end():
    merged = {'start': 944.07, 'end': 1109.8, 'merged_protected_start': 944.07,
              'merged_protected_end': 1109.8, 'merged_member_spans': [
        {'start': 944.07, 'end': 1074.76, 'stage': 'claude', 'confidence': 0.98,
         'precise_start': True, 'precise_end': True},
        {'start': 1051.8, 'end': 1081.37, 'stage': 'text_pattern'},
        {'start': 1046.0, 'end': 1109.8, 'stage': 'fingerprint',
         'fingerprint_match_start': 1046.0, 'fingerprint_match_end': 1109.8}]}

    assert edge_support(merged, 'end', MIN_CONF)['measured'] == 1074.76
    assert [(m['start'], m['end']) for m in hard_member_spans(merged, 944.07, 1109.8, MIN_CONF)] == [
        (944.07, 1074.76), (1051.8, 1074.76), (1046.0, 1074.76)]


def test_fingerprint_wholly_past_a_precise_end_keeps_its_correlation_window():
    merged = {'start': 72.66, 'end': 173.6, 'merged_distinct_ads': True,
              'merged_protected_start': 91.6, 'merged_protected_end': 173.6,
              'merged_member_spans': [
                  {'start': 91.6, 'end': 116.2, 'stage': 'claude', 'confidence': 0.97,
                   'precise_start': True, 'precise_end': True},
                  {'start': 130.0, 'end': 173.6, 'stage': 'fingerprint',
                   'fingerprint_match_start': 130.0, 'fingerprint_match_end': 173.6}]}

    support = edge_support(merged, 'end', MIN_CONF)
    assert (support['envelope'], support['measured'], support['source']) == (
        173.6, 140.0, 'fingerprint')
    assert [(m['start'], m['end']) for m in hard_member_spans(merged, 72.66, 173.6, MIN_CONF)] == [
        (91.6, 116.2), (130.0, 140.0)]


def test_fingerprint_start_before_a_precise_start_stays_measured():
    merged = {'start': 1700.0, 'end': 1836.66, 'merged_protected_start': 1700.0,
              'merged_protected_end': 1836.66, 'merged_member_spans': [
                  {'start': 1711.02, 'end': 1836.66, 'stage': 'claude', 'confidence': 0.98,
                   'precise_start': True, 'precise_end': True},
                  {'start': 1700.0, 'end': 1760.0, 'stage': 'fingerprint',
                   'fingerprint_match_start': 1700.0, 'fingerprint_match_end': 1760.0}]}

    fingerprint = [m for m in hard_member_spans(merged, 1700.0, 1836.66, MIN_CONF)
                   if m['stage'] == 'fingerprint']
    assert [(m['start'], m['end']) for m in fingerprint] == [(1700.0, 1760.0)]
    assert edge_support(merged, 'start', MIN_CONF)['measured'] == 1700.0


@pytest.mark.parametrize('pattern', [(30.0, 60.0), (100.0, 130.0), (-40.0, 0.0)])
def test_text_pattern_outside_precise_edges_stays_hard(pattern):
    merged = {'start': -40.0, 'end': 130.0, 'merged_protected_start': -40.0,
              'merged_protected_end': 130.0, 'merged_member_spans': [
                  {'start': 0.0, 'end': 30.0, 'stage': 'claude', 'confidence': 0.98,
                   'precise_start': True, 'precise_end': True},
                  {'start': pattern[0], 'end': pattern[1], 'stage': 'text_pattern'}]}

    text = [m for m in hard_member_spans(merged, -40.0, 130.0, MIN_CONF)
            if m['stage'] == 'text_pattern']
    assert [(m['start'], m['end']) for m in text] == [pattern]


def test_fingerprint_only_marker_is_measured_at_its_match_bounds():
    marker = _ad(100.0, 175.0, 'fingerprint', fingerprint_match_start=100.0,
                 fingerprint_match_end=160.0)

    assert edge_support(marker, 'end', MIN_CONF) == {
        'envelope': 175.0, 'measured': 160.0, 'source': 'fingerprint', 'precise': False}


def test_stale_precise_flag_no_longer_softens_the_fingerprint():
    merged = _envelope()
    clip_merge_spans(merged, 1711.02, 1830.0)
    merged['end'] = 1830.0

    support = edge_support(merged, 'end', MIN_CONF)
    assert support['measured'] == 1830.0
    assert support['precise'] is False
    fingerprint = [m for m in hard_member_spans(merged, 1711.02, 1830.0, MIN_CONF)
                   if m['stage'] == 'fingerprint']
    assert [(m['start'], m['end']) for m in fingerprint] == [(1770.0, 1830.0)]


@pytest.mark.parametrize('confidence', [0.5, 0.99])
def test_category_conflicting_member_never_contributes_a_measured_edge(confidence):
    marker = {'start': 1700.0, 'end': 1872.74, 'merged_protected_start': 1700.0,
              'merged_protected_end': 1872.74, 'merged_member_spans': [
        {'start': 1700.0, 'end': 1800.0, 'stage': 'keep_content', 'confidence': confidence,
         'precise_start': True, 'precise_end': True},
        {'start': 1770.0, 'end': 1872.74, 'stage': 'fingerprint',
         'fingerprint_match_start': 1770.0, 'fingerprint_match_end': 1872.74}]}

    assert edge_support(marker, 'end', MIN_CONF)['measured'] == 1872.74
    assert edge_support(marker, 'start', MIN_CONF)['measured'] == 1770.0


def test_low_confidence_precise_member_does_not_soften_the_fingerprint():
    merged = _envelope()
    for member in merged['merged_member_spans']:
        member['confidence'] = 0.5

    support = edge_support(merged, 'end', MIN_CONF)
    assert (support['measured'], support['source']) == (1872.74, 'fingerprint')


def test_clip_merge_spans_drops_provenance_outside_an_approved_trim():
    merged = _envelope()
    merged.update(fingerprint_match_start=1770.0, fingerprint_match_end=1872.74,
                  dai_core_spans=[{'start': 1714.33, 'end': 1860.0}])

    clip_merge_spans(merged, 1711.02, 1836.66)

    members = recorded_member_spans(merged)
    assert members and all(m['end'] <= 1836.66 for m in members)
    assert all(m.get('fingerprint_match_end', 0.0) <= 1836.66 for m in members)
    assert merged['fingerprint_match_end'] == 1836.66
    assert dai_core_bounds(merged)[1] == 1836.66


def test_clip_merge_spans_drops_a_fingerprint_member_whose_match_leaves_the_range():
    marker = {'start': 0.0, 'end': 200.0, 'fingerprint_match_start': 150.0,
              'fingerprint_match_end': 200.0, 'merged_member_spans': [
                  {'start': 0.0, 'end': 120.0, 'stage': 'claude', 'confidence': 0.9},
                  {'start': 90.0, 'end': 200.0, 'stage': 'fingerprint',
                   'fingerprint_match_start': 150.0, 'fingerprint_match_end': 200.0}]}

    clip_merge_spans(marker, 0.0, 120.0)

    assert [m['stage'] for m in recorded_member_spans(marker)] == ['claude']
    assert 'fingerprint_match_start' not in marker
    assert 'fingerprint_match_end' not in marker


def test_member_spans_record_sponsor_and_category():
    base = _ad(0.0, 40.0, 'claude', sponsor='example-podcast', category='Self-Promo')
    note_merged_members(base, _ad(41.0, 130.0, 'fingerprint', sponsor='Acme Tools',
                                  category='sponsor'))

    labels = [(m.get('sponsor'), m.get('category')) for m in base['merged_member_spans']]
    assert labels == [('example-podcast', 'self_promo'), ('Acme Tools', 'sponsor')]
    assert [(m.get('sponsor'), m.get('category'))
            for m in recorded_member_spans(base)] == labels


def test_member_spans_leave_unknown_labels_unset():
    base = _ad(0.0, 40.0, 'claude', sponsor='', category='pre-roll')
    note_merged_members(base, _ad(41.0, 80.0, 'claude'))

    assert all('sponsor' not in m and 'category' not in m
               for m in base['merged_member_spans'])


def test_coalesce_keeps_differently_labeled_windows_apart():
    base = _ad(0.0, 60.0, 'claude', sponsor='example-podcast', category='self_promo')
    note_merged_members(base, _ad(40.0, 130.0, 'claude', sponsor='Acme Tools',
                                  category='sponsor'))
    base['end'] = 130.0
    note_merged_members(base, _ad(100.0, 150.0, 'claude', sponsor='acme tools',
                                  category='sponsor'))

    assert member_bases(base['merged_member_spans']) == [
        {'start': 0.0, 'end': 60.0, 'stage': 'claude'},
        {'start': 40.0, 'end': 150.0, 'stage': 'claude'}]
