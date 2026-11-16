"""snip_short_asks: floors, carve, boundary clamp and the cut-time filter."""
import pytest

from tests.app_bootstrap import bootstrap

bootstrap('snip_short_asks_floors_test_')

from ad_validator import AdValidator, Decision
from audio_processor import AudioProcessor
from main_app.pipeline import cuts, detect


def _flagged(start=100.0, end=106.0, category='interaction', **extra):
    ad = {'start': start, 'end': end, 'confidence': 0.9, 'category': category,
          'reason': 'Host asks listeners to subscribe', 'detection_stage': 'claude',
          'short_ask': True, 'short_ask_origin': [start, end]}
    ad.update(extra)
    return ad


def _plain(**extra):
    ad = _flagged(**extra)
    ad.pop('short_ask')
    ad.pop('short_ask_origin')
    return ad


def _validate(ad):
    return AdValidator(episode_duration=3600.0, segments=[]).validate([ad]).ads[0]


class TestValidatorFloor:
    @pytest.mark.parametrize('category', ['interaction', 'self_promo', 'sponsor'])
    def test_flagged_six_seconds_is_not_a_very_short_error(self, category):
        ad = _validate(_flagged(category=category))
        assert not any('Very short' in f for f in ad['validation']['flags'])
        assert ad['validation']['decision'] == Decision.ACCEPT.value

    def test_unflagged_six_seconds_is_rejected(self):
        ad = _validate(_plain())
        assert any('Very short' in f for f in ad['validation']['flags'])
        assert ad['validation']['decision'] == Decision.REJECT.value

    def test_flagged_under_the_short_floor_is_rejected(self):
        ad = _validate(_flagged(end=101.0))
        assert any('Very short' in f for f in ad['validation']['flags'])

    def test_flagged_over_forty_seconds_uses_normal_floors(self):
        ad = _validate(_flagged(end=150.0, category='interaction'))
        assert not any('Very short' in f for f in ad['validation']['flags'])


class TestCutFilter:
    @staticmethod
    def _cuts(ad):
        return AudioProcessor().compute_applied_cuts([ad], 3600.0)

    def test_flagged_short_ask_is_cut(self):
        applied = self._cuts(_flagged(confidence=0.7))
        assert [(c['start'], c['end']) for c in applied] == [(100.0, 106.0)]

    def test_unflagged_low_confidence_six_seconds_is_skipped(self):
        assert self._cuts(_plain(confidence=0.7)) == []

    def test_merge_with_an_unflagged_neighbour_drops_the_flag(self):
        applied = self._cuts_many([_flagged(start=100.0, end=106.0),
                                   _plain(start=106.5, end=112.0)])
        assert len(applied) == 1
        assert 'short_ask' not in applied[0] and 'category' not in applied[0]

    def test_unflagged_cut_does_not_gain_a_category(self):
        applied = self._cuts(_plain(start=100.0, end=130.0))
        assert 'category' not in applied[0]

    @staticmethod
    def _cuts_many(ads):
        return AudioProcessor().compute_applied_cuts(ads, 3600.0)

    def test_flag_lost_past_the_cap_uses_normal_floor(self):
        ad = _flagged(confidence=0.7, end=108.0, short_ask_origin=[100.0, 106.0])
        assert self._cuts(ad) == []


class TestCarve:
    def test_flagged_piece_with_a_keep_made_edge_gets_the_normal_floor(self):
        ad = _flagged(start=100.0, end=107.0)
        out = cuts._carve_cuts_around([ad], [ad], [{'start': 100.0, 'end': 101.0}])
        assert out == []

    def test_flagged_mid_sentence_fragment_of_three_seconds_is_not_cut(self):
        ad = _flagged(start=100.0, end=109.0)
        out = cuts._carve_cuts_around([ad], [ad], [{'start': 103.0, 'end': 109.0}])
        assert out == []

    def test_unflagged_piece_under_seven_seconds_is_dropped(self):
        ad = _plain(start=100.0, end=107.0)
        out = cuts._carve_cuts_around([ad], [ad], [{'start': 100.0, 'end': 101.0}])
        assert out == []


class TestReviewerTrimFloor:
    @staticmethod
    def _run(ad):
        from tests.unit.test_ad_reviewer_contradiction import _resp, _run_affirmed_confirm
        return _run_affirmed_confirm(_resp('{"ad_start": 100.0, "ad_end": 107.0}'), ad=ad)[0]

    def test_flagged_short_trim_is_not_held(self):
        result = self._run(_flagged(start=100.0, end=108.0))
        assert result.accepted_after_review[0]['end'] == 107.0

    def test_unflagged_short_trim_is_held_without_bounds(self):
        result = self._run(_plain(start=100.0, end=108.0))
        assert result.accepted_after_review[0]['end'] == 108.0


def _segs():
    return [
        {'start': 90.0, 'end': 100.0, 'text': 'That was the story.'},
        {'start': 100.0, 'end': 106.0, 'text': 'Please subscribe to the show.'},
        {'start': 106.0, 'end': 115.0, 'text': 'Now back to it.'},
    ]


class TestRefineClamp:
    def test_refine_boundaries_snaps_a_flagged_ask_to_sentence_edges(self):
        ad = _flagged(start=100.0, end=106.0)
        ad['start'], ad['end'] = 99.5, 106.4
        out = detect._refine_boundaries([dict(ad)], _segs(), db=None)
        assert out[0]['short_ask'] is True
        assert (out[0]['start'], out[0]['end']) == (100.0, 106.0)

    def test_without_a_transcript_the_flag_is_dropped(self):
        out = detect._refine_boundaries([_flagged()], [], db=None)
        assert 'short_ask' not in out[0]

    def test_growth_beyond_the_cap_is_pulled_back(self):
        ad = _flagged(start=100.0, end=106.0)
        ad['end'] = 110.0
        out = detect._clamp_short_asks([ad], _segs())
        assert out[0]['end'] == 106.0 and out[0]['short_ask'] is True

    def test_a_merged_marker_past_the_length_cap_is_not_clamped_as_a_short_ask(self):
        ad = _flagged(start=100.0, end=106.0)
        ad['end'] = 160.0
        master = dict(ad)
        out = detect._clamp_short_asks([ad], _segs(), masters=[master])
        assert (out[0]['start'], out[0]['end']) == (100.0, 160.0)
        assert 'short_ask' not in out[0] and 'short_ask' not in master
        assert 'short_ask_origin' not in master

    def test_no_sentence_edge_drops_the_flag(self):
        segs = [{'start': 99.0, 'end': 103.0, 'text': 'if you like the show and'},
                {'start': 103.0, 'end': 108.0, 'text': 'want more then'}]
        out = detect._clamp_short_asks([_flagged()], segs)
        assert 'short_ask' not in out[0]
