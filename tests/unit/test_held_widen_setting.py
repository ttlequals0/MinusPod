"""held_widen_seconds: the global setting that widens a held marker's edit window."""

import pytest

import config
from database.settings import registry_get_default
from utils.markers import held_marker_window

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

    def test_default_ignores_neighbours_and_episode_edges(self):
        # The +/-0.5 s tolerance is never limited by a neighbour.
        near = _cut(140.2, 160.0)
        assert held_marker_window([MARKER, near], MARKER, 140.3, True) == (99.5, 140.5)

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
