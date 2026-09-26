import os
import sys
import tempfile

import pytest

os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='mergemem_test_'))
os.environ.setdefault('SECRET_KEY', 'test-secret')
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from ad_detector.boundaries import (
    deduplicate_window_ads,
    split_conflicting_action_span,
)
from utils.markers import (
    carve_fragment,
    clip_dai_core_spans,
    clip_merge_spans,
    dai_core_bounds,
    estimated_text_bounds,
    mark_distinct_merge,
    merge_dai_core_spans,
    note_merged_members,
    protected_member_spans,
)


def _base(members):
    """Member spans reduced to start, end and stage."""
    if isinstance(members, dict):
        return {k: members[k] for k in ('start', 'end', 'stage')}
    return [_base(m) for m in members]

def _ad(start, end, stage, **extra):
    return {'start': start, 'end': end, 'detection_stage': stage, **extra}


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

    assert _base(base['merged_member_spans']) == [
        {'start': 100.0, 'end': 130.0, 'stage': 'claude'},
        {'start': 131.0, 'end': 160.0, 'stage': 'text_pattern'},
        {'start': 161.0, 'end': 200.0, 'stage': 'claude'},
    ]
    assert base['merged_protected_start'] == 100.0
    assert base['merged_protected_end'] == 200.0


def test_unprotected_members_contribute_no_span():
    base = _ad(837.2, 1040.0, 'dai_differential')
    note_merged_members(base, _ad(1041.0, 1068.5, 'dai_differential'))
    assert _base(base['merged_member_spans']) == []


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

    assert _base(base['merged_member_spans']) == [
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

    assert _base(base['merged_member_spans']) == [
        {'start': 100.0, 'end': 290.0, 'stage': 'text_pattern'},
        {'start': 320.0, 'end': 360.0, 'stage': None},
    ]


def test_estimated_text_pattern_member_records_only_its_matched_text():
    # No outro matched, so the end is the pattern's average duration, not
    # evidence.
    base = _ad(649.4, 921.1, 'claude')
    other = _estimated(831.75, 999.65, 831.75, 860.0)

    note_merged_members(base, other)

    assert _base(base['merged_member_spans']) == [
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

    assert _base(base['merged_member_spans'][1]) == {
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

    assert _base(base['merged_member_spans']) == [
        {'start': 649.4, 'end': 921.1, 'stage': 'claude'}]
    assert base['merged_protected_end'] == 921.1


def test_estimate_moved_by_a_snap_still_narrows_to_its_text():
    # A snap that moved the end does not turn the estimated tail into evidence.
    base = _ad(649.4, 700.0, 'claude')

    note_merged_members(base, _estimated(831.75, 1000.05, 831.75, 860.0))

    assert _base(base['merged_member_spans'][1]) == {
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
    assert _base(base['merged_member_spans']) == members

    later = _ad(1100.0, 1200.0, 'claude')
    note_merged_members(later, base)

    assert _base(later['merged_member_spans'][1:]) == members


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

    assert _base(base['merged_member_spans']) == expected


def test_overlapping_members_of_different_stages_stay_separate():
    base = _ad(100.0, 300.0, 'claude')

    note_merged_members(base, _ad(150.0, 400.0, 'text_pattern'))

    assert _base(base['merged_member_spans']) == [
        {'start': 100.0, 'end': 300.0, 'stage': 'claude'},
        {'start': 150.0, 'end': 400.0, 'stage': 'text_pattern'}]


def test_clip_merge_spans_narrows_the_union_and_its_members():
    # What every hand-narrowing site (split piece, approved trim, boundary
    # adjustment) owes the records the wider span left behind.
    marker = _ad(100.0, 150.0, 'claude')
    mark_distinct_merge(marker, _ad(160.0, 300.0, 'text_pattern'))
    marker['end'] = 300.0

    clip_merge_spans(marker, 100.0, 150.0)

    assert _base(marker['merged_member_spans']) == [
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

    assert _base(merged[0]['merged_member_spans']) == [
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

