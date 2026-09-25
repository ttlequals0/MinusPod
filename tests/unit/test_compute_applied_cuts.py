"""compute_applied_cuts reproduces the merge/filter/end-trim remove_ads applies.

Assets and the verification timestamp map consume this list, so it must match
what ffmpeg actually cut, not the requested segments.
"""

import logging

import pytest

from audio_processor import AudioProcessor


@pytest.fixture
def processor():
    return AudioProcessor()


def test_empty_input_returns_empty(processor):
    assert processor.compute_applied_cuts([], 600.0) == []
    assert processor.compute_applied_cuts([{'start': 10.0, 'end': 30.0}], 0) == []


def test_close_cuts_merge_with_joined_reason(processor):
    beep = processor.get_beep_duration()
    cuts = processor.compute_applied_cuts(
        [{'start': 100.0, 'end': 130.0, 'reason': 'a'},
         {'start': 130.5, 'end': 160.0, 'reason': 'b'}],
        600.0,
    )
    assert cuts == [{'start': 100.0, 'end': 160.0, 'reason': 'a; b',
                     'replacement_duration': beep}]


def test_contained_cut_merges_to_outer_end(processor):
    beep = processor.get_beep_duration()
    cuts = processor.compute_applied_cuts(
        [{'start': 100.0, 'end': 160.0}, {'start': 110.0, 'end': 120.0}],
        600.0,
    )
    assert cuts == [{'start': 100.0, 'end': 160.0, 'replacement_duration': beep}]


def test_overlapping_cuts_merge_despite_surrounding_barrier(processor):
    beep = processor.get_beep_duration()
    cuts = processor.compute_applied_cuts(
        [{'start': 100.0, 'end': 160.0}, {'start': 110.0, 'end': 120.0}],
        600.0,
        cut_barriers=[{'start': 90.0, 'end': 170.0}],
    )
    assert cuts == [{'start': 100.0, 'end': 160.0, 'replacement_duration': beep}]


def test_short_cut_dropped(processor):
    beep = processor.get_beep_duration()
    cuts = processor.compute_applied_cuts(
        [{'start': 100.0, 'end': 105.0}, {'start': 200.0, 'end': 230.0}],
        600.0,
    )
    assert cuts == [{'start': 200.0, 'end': 230.0, 'replacement_duration': beep}]


def test_unsorted_input_sorted_before_merge(processor):
    cuts = processor.compute_applied_cuts(
        [{'start': 300.0, 'end': 330.0}, {'start': 100.0, 'end': 130.0}],
        600.0,
    )
    assert [c['start'] for c in cuts] == [100.0, 300.0]


def test_end_of_episode_cut_extends_to_total_duration(processor):
    beep = processor.get_beep_duration()
    requested = [{'start': 500.0, 'end': 580.0}]
    cuts = processor.compute_applied_cuts(requested, 600.0)
    assert cuts == [{'start': 500.0, 'end': 600.0, 'replacement_duration': beep}]
    # Caller's list is not mutated (it is reused for UI/finalize)
    assert requested[0]['end'] == 580.0


def test_confirmed_cut_keeps_unapproved_tail_and_adjacent_candidate_separate(processor):
    approved = {'start': 80.0, 'end': 100.0,
                'validation': {'user_confirmed': True,
                               'confirmed_span': {'start': 80.0, 'end': 100.0}}}
    cuts = processor.compute_applied_cuts([approved], 115.0)
    assert [(cut['start'], cut['end']) for cut in cuts] == [(80.0, 100.0)]

    cuts = processor.compute_applied_cuts(
        [approved, {'start': 100.4, 'end': 111.0}], 200.0)
    assert [(cut['start'], cut['end']) for cut in cuts] == [
        (80.0, 100.0), (100.4, 111.0)]

    second_approved = dict(approved, start=99.0, end=110.0,
                           validation={'user_confirmed': True,
                                       'confirmed_span': {'start': 99.0,
                                                          'end': 110.0}})
    cuts = processor.compute_applied_cuts([approved, second_approved], 200.0)
    assert [(cut['start'], cut['end']) for cut in cuts] == [(80.0, 110.0)]

    separated = dict(second_approved, start=100.4, end=110.0,
                     validation={'user_confirmed': True,
                                 'confirmed_span': {'start': 100.4,
                                                    'end': 110.0}})
    cuts = processor.compute_applied_cuts([approved, separated], 200.0)
    assert [(cut['start'], cut['end']) for cut in cuts] == [
        (80.0, 100.0), (100.4, 110.0)]

    short = dict(approved, start=95.0, confidence=0.4,
                 validation={'user_confirmed': True,
                             'confirmed_span': {'start': 95.0, 'end': 100.0}})
    cuts = processor.compute_applied_cuts([short], 115.0)
    assert [(cut['start'], cut['end']) for cut in cuts] == [(95.0, 100.0)]


def test_precise_word_end_blocks_render_extension_and_gap_merge(processor):
    precise = {'start': 80.0, 'end': 100.0, 'confidence': 0.95,
               'word_timed_end': 100.0}
    cuts = processor.compute_applied_cuts([precise], 115.0)
    assert [(cut['start'], cut['end']) for cut in cuts] == [(80.0, 100.0)]

    cuts = processor.compute_applied_cuts(
        [precise, {'start': 100.4, 'end': 120.0}], 200.0)
    assert [(cut['start'], cut['end']) for cut in cuts] == [
        (80.0, 100.0), (100.4, 120.0)]


def test_kept_tail_blocks_end_of_episode_extension(processor):
    beep = processor.get_beep_duration()
    cuts = processor.compute_applied_cuts(
        [{'start': 100.0, 'end': 140.0}], 165.0,
        cut_barriers=[{'start': 145.0, 'end': 160.0}],
    )

    assert cuts == [
        {'start': 100.0, 'end': 140.0, 'replacement_duration': beep},
    ]


def test_earlier_keep_does_not_block_trailing_extension(processor):
    beep = processor.get_beep_duration()
    cuts = processor.compute_applied_cuts(
        [{'start': 130.0, 'end': 165.0}], 170.0,
        cut_barriers=[{'start': 100.0, 'end': 120.0}],
    )

    assert cuts == [
        {'start': 130.0, 'end': 170.0, 'replacement_duration': beep},
    ]


def test_keep_barrier_prevents_cut_fragments_from_remerging(processor):
    beep = processor.get_beep_duration()
    cuts = processor.compute_applied_cuts(
        [{'start': 100.0, 'end': 112.0},
         {'start': 112.5, 'end': 125.0}], 600.0,
        cut_barriers=[{'start': 112.0, 'end': 112.5}],
    )

    assert cuts == [
        {'start': 100.0, 'end': 112.0, 'replacement_duration': beep},
        {'start': 112.5, 'end': 125.0, 'replacement_duration': beep},
    ]


def test_no_end_trim_when_enough_content_remains(processor):
    beep = processor.get_beep_duration()
    cuts = processor.compute_applied_cuts([{'start': 500.0, 'end': 560.0}], 600.0)
    assert cuts == [{'start': 500.0, 'end': 560.0, 'replacement_duration': beep}]


def test_negative_start_clamped_to_zero(processor):
    beep = processor.get_beep_duration()
    requested = [{'start': -5.0, 'end': 30.0}]
    cuts = processor.compute_applied_cuts(requested, 600.0)
    assert cuts == [{'start': 0.0, 'end': 30.0, 'replacement_duration': beep}]
    assert requested[0]['start'] == -5.0


def test_end_past_duration_clamped(processor):
    # Clamp lands the end on total_duration; the end-trim then keeps it there.
    beep = processor.get_beep_duration()
    cuts = processor.compute_applied_cuts([{'start': 500.0, 'end': 650.0}], 600.0)
    assert cuts == [{'start': 500.0, 'end': 600.0, 'replacement_duration': beep}]


def test_fully_out_of_range_cut_dropped(processor):
    beep = processor.get_beep_duration()
    cuts = processor.compute_applied_cuts(
        [{'start': 610.0, 'end': 640.0}, {'start': 100.0, 'end': 130.0}],
        600.0,
    )
    assert cuts == [{'start': 100.0, 'end': 130.0, 'replacement_duration': beep}]


def test_short_low_confidence_cut_dropped(processor):
    cuts = processor.compute_applied_cuts(
        [{'start': 100.0, 'end': 107.0, 'confidence': 0.7,
          'detection_stage': 'claude'}],
        600.0,
    )
    assert cuts == []


def test_short_high_confidence_cut_kept(processor):
    cuts = processor.compute_applied_cuts(
        [{'start': 100.0, 'end': 107.0, 'confidence': 0.95,
          'detection_stage': 'claude'}],
        600.0,
    )
    assert len(cuts) == 1
    assert cuts[0]['start'] == 100.0 and cuts[0]['end'] == 107.0


def test_short_fingerprint_cut_kept_regardless_of_confidence(processor):
    cuts = processor.compute_applied_cuts(
        [{'start': 100.0, 'end': 106.0, 'confidence': 0.5,
          'detection_stage': 'fingerprint'}],
        600.0,
    )
    assert len(cuts) == 1


def test_short_cut_without_trust_fields_dropped(processor):
    # No confidence/stage at all (older callers): old behavior preserved.
    cuts = processor.compute_applied_cuts([{'start': 100.0, 'end': 107.0}], 600.0)
    assert cuts == []


def test_short_fragment_split_from_valid_cut_is_kept(processor):
    cuts = processor.compute_applied_cuts(
        [{'start': 100.0, 'end': 108.0, 'confidence': 0.85,
          '_measured_split_fragment': True}],
        600.0,
    )

    assert len(cuts) == 1
    assert cuts[0]['start'] == 100.0
    assert cuts[0]['end'] == 108.0
    assert '_measured_split_fragment' not in cuts[0]


def test_merge_carries_strongest_trust_signal(processor):
    # Two sub-10s spans merge into one >10s span; survives the floor anyway,
    # but the merged dict must carry the max confidence and fingerprint stage
    # of its members.
    cuts = processor.compute_applied_cuts(
        [{'start': 100.0, 'end': 106.0, 'confidence': 0.6},
         {'start': 106.5, 'end': 112.0, 'confidence': 0.95,
          'detection_stage': 'fingerprint'}],
        600.0,
    )
    assert len(cuts) == 1
    assert cuts[0]['confidence'] == 0.95
    assert cuts[0]['detection_stage'] == 'fingerprint'


def test_applied_totals_logged(processor, caplog):
    """Requested-vs-applied totals are logged so production marker-vs-cut
    deltas are attributable (spec 1.5 instrumentation)."""
    with caplog.at_level(logging.INFO, logger='audio_processor'):
        processor.compute_applied_cuts(
            [{'start': 100.0, 'end': 130.0}, {'start': 130.5, 'end': 160.0},
             {'start': 500.0, 'end': 505.0}],
            600.0,
        )
    msg = next(r.message for r in caplog.records
               if r.message.startswith('Applied cuts:'))
    # First two merge (0.5s gap absorbed), the 5s cut drops as short.
    assert '1 cut(s) totaling 60.0s' in msg
    assert 'requested 3 totaling 64.5s' in msg
