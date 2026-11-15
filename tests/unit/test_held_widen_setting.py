"""held_widen_seconds: the hidden tunable that widens a held marker's edit window."""

import pytest

import config
from utils.markers import held_marker_window

MARKER = {'start': 100.0, 'end': 140.0, 'held_for_review': True}


def _cut(start, end):
    return {'start': start, 'end': end, 'was_cut': True}


class TestResolver:
    def test_default_is_zero(self, monkeypatch):
        monkeypatch.delenv('HELD_WIDEN_SECONDS', raising=False)
        assert config.STAGE_TUNABLE_DEFAULTS['held_widen_seconds'] == 0.0
        assert config.resolve_held_widen_seconds() == 0.0

    def test_env_supplies_a_float(self, monkeypatch):
        monkeypatch.setenv('HELD_WIDEN_SECONDS', '45.5')
        assert config.resolve_held_widen_seconds() == 45.5

    def test_stored_value_wins_over_env(self, monkeypatch):
        monkeypatch.setenv('HELD_WIDEN_SECONDS', '30')
        settings = {'held_widen_seconds': {'value': '60', 'is_default': False}}
        assert config.get_stage_tunable('held_widen_seconds', settings=settings) == 60.0

    @pytest.mark.parametrize('bad', ['-1', '121', 'abc'])
    def test_out_of_range_or_malformed_falls_back_to_default(self, monkeypatch, bad):
        monkeypatch.setenv('HELD_WIDEN_SECONDS', bad)
        assert config.resolve_held_widen_seconds() == 0.0

    def test_ceiling_is_accepted(self, monkeypatch):
        monkeypatch.setenv('HELD_WIDEN_SECONDS', '120')
        assert config.resolve_held_widen_seconds() == 120.0


class TestWindow:
    def test_default_is_the_reviewed_span_plus_tolerance(self):
        assert held_marker_window([MARKER], MARKER, 3600.0, True) == (99.5, 140.5)

    def test_default_ignores_neighbours_and_episode_edges(self):
        # Upstream never limited the +/-0.5 s tolerance by a neighbour.
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
