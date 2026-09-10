"""A keep-mapped category must not shelter a dynamically inserted ad (#728).

`cross_promo` covers both a guest plugging their own show, which is why an
operator sets keep, and a paid injected ad for another podcast. The category
cannot separate them; the cross-fetch differential can.
"""
from tests.app_bootstrap import bootstrap

bootstrap('keep_differential_test_', passphrase='keep-differential-test-pass')

from main_app.processing import (  # noqa: E402
    KeepDifferentialOverride,
    _apply_late_keep_safety_net,
    _partition_keep_ads,
    _partition_pass2_category_actions,
)

ACTIONS = {'cross_promo': 'keep', 'self_promo': 'keep', 'sponsor': 'remove'}

# The reporter's episode: one contiguous break, the middle span kept by
# category while sitting inside a region measured at corr=0.056.
DIFFERENTIAL = {'regions': [
    {'kind': 'identical', 'start_s': 5387.2, 'end_s': 5504.8, 'corr': 0.992},
    {'kind': 'differential', 'start_s': 5504.8, 'end_s': 5519.9, 'corr': 0.027},
    {'kind': 'differential', 'start_s': 5519.9, 'end_s': 5549.7, 'corr': 0.059},
    {'kind': 'differential', 'start_s': 5549.7, 'end_s': 5580.3, 'corr': 0.056},
    {'kind': 'unknown', 'start_s': 5580.3, 'end_s': 5613.1, 'corr': None},
]}


def _ad(start, end, category='cross_promo', **extra):
    return {'start': start, 'end': end, 'category': category, **extra}


def _override(enabled=True, corr_max=0.60, differential=DIFFERENTIAL):
    return KeepDifferentialOverride(differential, corr_max=corr_max, enabled=enabled)


def test_injected_cross_promo_is_cut_despite_keep():
    ad = _ad(5549.7, 5584.6)
    keep, remove = _partition_keep_ads([ad], ACTIONS, _override())
    assert keep == []
    assert remove == [ad]
    assert ad['keep_overridden_by_differential'] is True
    assert ad['keep_override_corr'] == 0.056


def test_organic_cross_promo_over_identical_audio_is_still_kept():
    ad = _ad(5400.0, 5480.0)
    keep, remove = _partition_keep_ads([ad], ACTIONS, _override())
    assert remove == []
    assert keep == [ad]
    assert ad['action_applied'] == 'keep'
    assert 'keep_overridden_by_differential' not in ad


def test_unknown_region_does_not_override_keep():
    """corr=None measured nothing, so it is not evidence of insertion."""
    ad = _ad(5585.0, 5610.0)
    keep, _ = _partition_keep_ads([ad], ACTIONS, _override())
    assert keep == [ad]


def test_high_corr_differential_region_does_not_override_keep():
    """A region that mostly matched across fetches proves nothing."""
    weak = {'regions': [{'kind': 'differential', 'start_s': 100.0,
                         'end_s': 200.0, 'corr': 0.95}]}
    ad = _ad(120.0, 180.0)
    keep, _ = _partition_keep_ads([ad], ACTIONS, _override(differential=weak))
    assert keep == [ad]


def test_setting_disabled_restores_the_old_behaviour():
    ad = _ad(5549.7, 5584.6)
    keep, remove = _partition_keep_ads([ad], ACTIONS, _override(enabled=False))
    assert remove == []
    assert keep == [ad]


def test_no_differential_data_keeps_the_marker():
    ad = _ad(5549.7, 5584.6)
    keep, _ = _partition_keep_ads([ad], ACTIONS, _override(differential=None))
    assert keep == [ad]


def test_remove_category_is_untouched_by_the_override():
    ad = _ad(5504.8, 5549.7, category='sponsor')
    keep, remove = _partition_keep_ads([ad], ACTIONS, _override())
    assert keep == []
    assert remove == [ad]
    assert 'keep_overridden_by_differential' not in ad


def test_pattern_override_still_wins_and_is_stamped_alone():
    ad = _ad(5400.0, 5480.0, pattern_defined=True)
    _, remove = _partition_keep_ads([ad], ACTIONS, _override())
    assert remove == [ad]
    assert ad['keep_overridden_by_pattern'] is True
    assert 'keep_overridden_by_differential' not in ad


def test_late_safety_net_applies_the_same_override():
    ad = _ad(5549.7, 5584.6)
    remaining = _apply_late_keep_safety_net([ad], [ad], ACTIONS, _override())
    assert remaining == [ad]
    assert ad['keep_overridden_by_differential'] is True


def test_late_safety_net_still_drops_an_organic_keep():
    ad = _ad(5400.0, 5480.0)
    remaining = _apply_late_keep_safety_net([ad], [ad], ACTIONS, _override())
    assert remaining == []
    assert ad['action_applied'] == 'keep'


def test_a_kept_marker_holds_no_stale_review_hold():
    ad = _ad(5400.0, 5480.0, held_for_review=True, hold_reason='short_ad')
    keep, _ = _partition_keep_ads([ad], ACTIONS, _override())
    assert keep == [ad]
    assert ad['held_for_review'] is False
    assert ad['hold_cleared_reason'] == 'short_ad'


class TestPass2:
    """Pass 2 carries the same standing overrides, or an injected ad that
    survived pass 1 is simply kept again."""

    def test_injected_cross_promo_is_cut_in_pass_two(self):
        original = _ad(5549.7, 5584.6)
        processed = _ad(120.0, 155.0)
        rem_p, rem_o, kept_p, kept_o = _partition_pass2_category_actions(
            [processed], [original], ACTIONS, _override())
        assert kept_o == [] and kept_p == []
        assert rem_o == [original]
        assert original['keep_overridden_by_differential'] is True
        assert processed['keep_overridden_by_differential'] is True

    def test_organic_cross_promo_is_still_kept_in_pass_two(self):
        original = _ad(5400.0, 5480.0)
        processed = _ad(60.0, 140.0)
        rem_p, rem_o, kept_p, kept_o = _partition_pass2_category_actions(
            [processed], [original], ACTIONS, _override())
        assert kept_o == [original]
        assert rem_o == []
        assert original['action_applied'] == 'keep'

    def test_processed_coordinates_are_not_used_for_the_overlap(self):
        """Regions are original-audio coordinates. A processed span that
        happens to land in a region must not decide this."""
        original = _ad(5400.0, 5480.0)
        processed = _ad(5549.7, 5584.6)
        _, _, _, kept_o = _partition_pass2_category_actions(
            [processed], [original], ACTIONS, _override())
        assert kept_o == [original]

    def test_pattern_override_still_wins_in_pass_two(self):
        original = _ad(5400.0, 5480.0, pattern_defined=True)
        processed = _ad(60.0, 140.0)
        _, rem_o, _, kept_o = _partition_pass2_category_actions(
            [processed], [original], ACTIONS, _override())
        assert kept_o == []
        assert rem_o == [original]
        assert original['keep_overridden_by_pattern'] is True
        assert 'keep_overridden_by_differential' not in original

    def test_disabled_setting_keeps_pass_two_behaviour(self):
        original = _ad(5549.7, 5584.6)
        processed = _ad(120.0, 155.0)
        _, _, _, kept_o = _partition_pass2_category_actions(
            [processed], [original], ACTIONS, _override(enabled=False))
        assert kept_o == [original]
