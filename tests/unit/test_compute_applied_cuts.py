"""compute_applied_cuts reproduces the merge/filter/end-trim remove_ads applies.

Assets and the verification timestamp map consume this list, so it must match
what ffmpeg actually cut, not the requested segments.
"""

import pytest

from audio_processor import AudioProcessor


@pytest.fixture
def processor():
    return AudioProcessor()


def test_empty_input_returns_empty(processor):
    assert processor.compute_applied_cuts([], 600.0) == []
    assert processor.compute_applied_cuts([{'start': 10.0, 'end': 30.0}], 0) == []


def test_close_cuts_merge_with_joined_reason(processor):
    cuts = processor.compute_applied_cuts(
        [{'start': 100.0, 'end': 130.0, 'reason': 'a'},
         {'start': 130.5, 'end': 160.0, 'reason': 'b'}],
        600.0,
    )
    assert cuts == [{'start': 100.0, 'end': 160.0, 'reason': 'a; b'}]


def test_contained_cut_merges_to_outer_end(processor):
    cuts = processor.compute_applied_cuts(
        [{'start': 100.0, 'end': 160.0}, {'start': 110.0, 'end': 120.0}],
        600.0,
    )
    assert cuts == [{'start': 100.0, 'end': 160.0}]


def test_short_cut_dropped(processor):
    cuts = processor.compute_applied_cuts(
        [{'start': 100.0, 'end': 105.0}, {'start': 200.0, 'end': 230.0}],
        600.0,
    )
    assert cuts == [{'start': 200.0, 'end': 230.0}]


def test_unsorted_input_sorted_before_merge(processor):
    cuts = processor.compute_applied_cuts(
        [{'start': 300.0, 'end': 330.0}, {'start': 100.0, 'end': 130.0}],
        600.0,
    )
    assert [c['start'] for c in cuts] == [100.0, 300.0]


def test_end_of_episode_cut_extends_to_total_duration(processor):
    requested = [{'start': 500.0, 'end': 580.0}]
    cuts = processor.compute_applied_cuts(requested, 600.0)
    assert cuts == [{'start': 500.0, 'end': 600.0}]
    # Caller's list is not mutated (it is reused for UI/finalize)
    assert requested[0]['end'] == 580.0


def test_no_end_trim_when_enough_content_remains(processor):
    cuts = processor.compute_applied_cuts([{'start': 500.0, 'end': 560.0}], 600.0)
    assert cuts == [{'start': 500.0, 'end': 560.0}]


def test_negative_start_clamped_to_zero(processor):
    requested = [{'start': -5.0, 'end': 30.0}]
    cuts = processor.compute_applied_cuts(requested, 600.0)
    assert cuts == [{'start': 0.0, 'end': 30.0}]
    assert requested[0]['start'] == -5.0


def test_end_past_duration_clamped(processor):
    # Clamp lands the end on total_duration; the end-trim then keeps it there.
    cuts = processor.compute_applied_cuts([{'start': 500.0, 'end': 650.0}], 600.0)
    assert cuts == [{'start': 500.0, 'end': 600.0}]


def test_fully_out_of_range_cut_dropped(processor):
    cuts = processor.compute_applied_cuts(
        [{'start': 610.0, 'end': 640.0}, {'start': 100.0, 'end': 130.0}],
        600.0,
    )
    assert cuts == [{'start': 100.0, 'end': 130.0}]
