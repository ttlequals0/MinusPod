import os
import sys
import tempfile

os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='mergemem_test_'))
os.environ.setdefault('SECRET_KEY', 'test-secret')
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from ad_detector.boundaries import (
    deduplicate_window_ads,
    split_conflicting_action_span,
)
from utils.markers import (
    clip_dai_core_spans,
    dai_core_bounds,
    mark_distinct_merge,
    merge_dai_core_spans,
    note_merged_members,
    protected_member_spans,
)


def _ad(start, end, stage):
    return {'start': start, 'end': end, 'detection_stage': stage}


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

    assert base['merged_member_spans'] == [
        {'start': 100.0, 'end': 130.0, 'stage': 'claude'},
        {'start': 131.0, 'end': 160.0, 'stage': 'text_pattern'},
        {'start': 161.0, 'end': 200.0, 'stage': 'claude'},
    ]
    assert base['merged_protected_start'] == 100.0
    assert base['merged_protected_end'] == 200.0


def test_unprotected_members_contribute_no_span():
    base = _ad(837.2, 1040.0, 'dai_differential')
    note_merged_members(base, _ad(1041.0, 1068.5, 'dai_differential'))
    assert base['merged_member_spans'] == []


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

    assert base['merged_member_spans'] == [
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

    assert base['merged_member_spans'] == [
        {'start': 100.0, 'end': 290.0, 'stage': 'text_pattern'},
        {'start': 320.0, 'end': 360.0, 'stage': None},
    ]


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

    assert merged[0]['merged_member_spans'] == [
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
