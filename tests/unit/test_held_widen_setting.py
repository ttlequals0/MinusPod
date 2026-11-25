"""held_widen_seconds: the global setting that widens a held marker's edit window."""

import pytest

import config
from database.settings import registry_get_default
from utils.markers import (held_marker_window, held_review_proposal, held_review_window,
                           usable_reviewer_proposal)

MARKER = {'start': 100.0, 'end': 140.0, 'held_for_review': True}


def _cut(start, end):
    return {'start': start, 'end': end, 'was_cut': True}


class _Db:
    def __init__(self, value=None, fail=False):
        self.value, self.fail = value, fail

    def get_setting(self, key):
        assert key == 'held_widen_seconds'
        if self.fail:
            raise RuntimeError('db down')
        return self.value


class TestResolver:
    def test_default_is_zero(self):
        assert config.resolve_held_widen_seconds(_Db(None)) == 0.0

    def test_reads_a_float(self):
        assert config.resolve_held_widen_seconds(_Db('45.5')) == 45.5

    @pytest.mark.parametrize('bad', ['-1', '121', 'abc', '', 'nan'])
    def test_out_of_range_or_malformed_is_zero(self, bad):
        assert config.resolve_held_widen_seconds(_Db(bad)) == 0.0

    def test_ceiling_is_accepted(self):
        assert config.resolve_held_widen_seconds(_Db('120')) == 120.0

    def test_a_read_error_fails_closed(self):
        assert config.resolve_held_widen_seconds(_Db(fail=True)) == 0.0

    def test_is_a_registry_setting_not_a_stage_tunable(self):
        assert 'held_widen_seconds' not in config.STAGE_TUNABLE_DEFAULTS
        assert registry_get_default('held_widen_seconds') == 0.0


class TestWindow:
    def test_default_is_the_reviewed_span_plus_tolerance(self):
        assert held_marker_window([MARKER], MARKER, 3600.0, True) == (99.5, 140.5)

    def test_default_ignores_neighbours_but_not_the_episode_end(self):
        # The +/-0.5 s tolerance is never limited by a neighbour; the API rejects off-episode bounds.
        near = _cut(140.2, 160.0)
        assert held_marker_window([MARKER, near], MARKER, 3600.0, True) == (99.5, 140.5)
        assert held_marker_window([MARKER, near], MARKER, 140.3, True) == (99.5, 140.3)

    def test_default_ignores_the_first_detected_bounds(self):
        trimmed = dict(MARKER, start=110.0, end=130.0,
                       reviewer_original_start=100.0, reviewer_original_end=140.0)
        assert held_marker_window([trimmed], trimmed, 3600.0, True) == (109.5, 130.5)

    def test_default_with_a_usable_proposal_is_the_envelope(self):
        assert held_marker_window(
            [MARKER], MARKER, 3600.0, True, proposal=(88.0, 131.0)) == (87.5, 140.5)

    def test_widened_reaches_sixty_seconds_each_way(self):
        assert held_marker_window(
            [MARKER], MARKER, 3600.0, True, widen_s=60.0) == (39.5, 200.5)

    def test_widened_uses_the_first_detected_bounds(self):
        trimmed = dict(MARKER, start=110.0, end=130.0,
                       reviewer_original_start=100.0, reviewer_original_end=140.0)
        assert held_marker_window(
            [trimmed], trimmed, 3600.0, True, widen_s=60.0) == (39.5, 200.5)

    def test_widened_stops_at_a_cut_neighbour_and_the_episode_edges(self):
        neighbour = _cut(90.0, 98.0)
        assert held_marker_window(
            [MARKER, neighbour], MARKER, 150.0, True, widen_s=60.0) == (98.0, 150.0)

    def test_widen_value_scales_the_window(self):
        assert held_marker_window(
            [MARKER], MARKER, 3600.0, True, widen_s=10.0) == (89.5, 150.5)

    def test_widened_does_not_cross_a_neighbour_overlapping_the_start(self):
        assert held_marker_window(
            [MARKER, _cut(90.0, 110.0)], MARKER, 3600.0, True, widen_s=30.0) == (100.0, 170.5)

    def test_widened_does_not_cross_a_rejected_span_overlapping_the_end(self):
        assert held_marker_window(
            [MARKER], MARKER, 3600.0, True, widen_s=30.0,
            blocked_spans=[{'start': 130.0, 'end': 150.0}]) == (69.5, 140.0)

    def test_widened_does_not_cross_a_neighbour_containing_the_span(self):
        assert held_marker_window(
            [MARKER, _cut(80.0, 160.0)], MARKER, 3600.0, True, widen_s=30.0) == (100.0, 140.0)

    def test_widened_ignores_a_neighbour_strictly_inside_the_span(self):
        assert held_marker_window(
            [MARKER, _cut(110.0, 120.0)], MARKER, 3600.0, True, widen_s=10.0) == (89.5, 150.5)


def _proposed(start, end, p_start, p_end):
    return {'start': start, 'end': end, 'held_for_review': True,
            'reviewer_proposed_start': p_start, 'reviewer_proposed_end': p_end}


class TestUsableProposal:
    def test_an_edge_inside_a_cut_neighbour_stops_at_it(self):
        marker = _proposed(100.0, 140.0, 88.0, 170.0)
        assert usable_reviewer_proposal(
            marker, 100.0, 140.0, 60.0, [(140.0, 200.0)]) == (88.0, 140.0)

    def test_the_clip_comes_before_the_shift_cap(self):
        # The end would move past the cap, but the neighbour stops it first.
        marker = _proposed(259.616, 359.8, 257.4, 419.856)
        assert usable_reviewer_proposal(
            marker, 259.616, 359.8, 60.0, [(359.8, 560.7)]) == (257.4, 359.8)

    def test_an_edge_over_the_cap_falls_back_to_the_marker_edge(self):
        marker = _proposed(100.0, 140.0, 30.0, 131.0)
        assert usable_reviewer_proposal(marker, 100.0, 140.0, 60.0) == (100.0, 131.0)

    def test_nothing_left_after_the_limits_is_no_proposal(self):
        marker = _proposed(100.0, 140.0, 30.0, 210.0)
        assert usable_reviewer_proposal(marker, 100.0, 140.0, 60.0) is None
        widened_into_cut = _proposed(100.0, 140.0, 100.0, 170.0)
        assert usable_reviewer_proposal(
            widened_into_cut, 100.0, 140.0, 60.0, [(140.0, 200.0)]) is None

    def test_a_blocker_overlapping_the_start_stops_the_start_at_the_marker_edge(self):
        marker = _proposed(100.0, 140.0, 85.0, 135.0)
        assert usable_reviewer_proposal(
            marker, 100.0, 140.0, 60.0, [(90.0, 110.0)]) == (100.0, 135.0)

    def test_a_shrinking_edge_over_the_cap_is_no_proposal(self):
        marker = _proposed(10286.7, 10534.6, 10422.1, 10527.9)
        assert usable_reviewer_proposal(marker, 10286.7, 10534.6, 60.0) is None

    def test_a_clipped_span_under_the_minimum_is_no_proposal(self):
        marker = _proposed(1054.6, 1101.5, 1101.4, 1132.3)
        assert usable_reviewer_proposal(
            marker, 1054.6, 1101.5, 60.0, [(1101.5, 1300.0)]) is None

    def test_a_disjoint_proposal_is_still_none(self):
        marker = _proposed(100.0, 140.0, 0.0, 12.0)
        assert usable_reviewer_proposal(marker, 100.0, 140.0, 60.0) is None


class TestHeldReviewProposal:
    EPISODE = {'status': 'processed', 'original_duration': 3600.0}

    def _markers(self):
        held = dict(_proposed(259.616, 359.8, 257.4, 419.856),
                    was_cut=False, hold_reason='reviewer_contradiction')
        return held, [{'start': 0.0, 'end': 146.7}, held, {'start': 359.8, 'end': 560.7}]

    def test_a_cut_neighbour_clips_the_proposal_with_widening_off(self):
        # Markers without was_cut on a processed episode were cut.
        held, markers = self._markers()
        assert held_review_proposal(self.EPISODE, markers, held, [], 60.0) == (257.4, 359.8)
        assert held_review_window(self.EPISODE, markers, held, [], 0.0, 60.0) == (256.9, 360.3)

    def test_an_undecided_held_neighbour_does_not_clip(self):
        held, markers = self._markers()
        held['reviewer_proposed_end'] = 400.0
        assert held_review_proposal(self.EPISODE, markers, held, [], 60.0) == (257.4, 359.8)
        markers[2] = dict(markers[2], was_cut=False, held_for_review=True)
        assert held_review_proposal(self.EPISODE, markers, held, [], 60.0) == (257.4, 400.0)

    def test_a_not_an_ad_span_clips_the_proposal(self):
        held = dict(_proposed(100.0, 140.0, 88.0, 170.0), was_cut=False)
        assert held_review_proposal(
            self.EPISODE, [held], held, [{'start': 150.0, 'end': 180.0}], 60.0) == (88.0, 150.0)

    def test_a_proposal_start_below_zero_clamps_to_zero(self):
        held = dict(_proposed(0.5, 40.0, -3.0, 30.0), was_cut=False)
        assert held_review_proposal(self.EPISODE, [held], held, [], 60.0) == (0.0, 30.0)


def test_window_stays_inside_the_episode_with_widening_off():
    marker = {'start': 0.3, 'end': 59.8}
    assert held_marker_window([marker], marker, 60.0, True) == (0.0, 60.0)
