"""Mark action: keep-like in the pipeline, chaptered instead of invisible.

Chapter-eligibility tests (held_status, min confidence, resolve_ad_chapter_config)
live in test_ad_chapters.py; this covers the pipeline partition/precedence side.
"""
from tests.app_bootstrap import bootstrap

bootstrap('mark_action_test_', passphrase='mark-action-test-pass')

from ad_detector.boundaries import effective_resolved_action  # noqa: E402
from config import SEGMENT_ACTIONS, is_keep_like  # noqa: E402
from main_app.processing import (  # noqa: E402
    KeepDifferentialOverride,
    _apply_late_keep_safety_net,
    _partition_keep_ads,
    _partition_pass2_category_actions,
    _stamp_and_carve_cuts,
)


def _marker(**kw):
    m = {'start': 10.0, 'end': 40.0, 'confidence': 1.0, 'category': 'sponsor'}
    m.update(kw)
    return m


def test_mark_joins_segment_actions():
    assert 'mark' in SEGMENT_ACTIONS


def test_is_keep_like():
    assert is_keep_like('keep') and is_keep_like('mark')
    assert not is_keep_like('remove') and not is_keep_like('beep') and not is_keep_like(None)


def test_mark_marker_is_not_cut_and_stamped_mark():
    keep, remove = _partition_keep_ads([_marker()], {'sponsor': 'mark'})
    assert remove == []
    assert len(keep) == 1
    assert keep[0]['was_cut'] is False
    assert keep[0]['action_applied'] == 'mark'


def test_mark_marker_contributes_no_time_saved():
    """A mark marker must not reach the rendered cut list: with a separate
    remove marker in play, the real stamp-and-carve step must produce a cut
    covering only the remove span, so time saved never counts the mark span."""
    actions = {'sponsor': 'mark', 'interaction': 'remove'}
    mark_marker = _marker(start=10.0, end=40.0)
    remove_marker = _marker(start=100.0, end=130.0, category='interaction')
    keep, remove = _partition_keep_ads([mark_marker, remove_marker], actions)
    assert [a['start'] for a in keep] == [10.0]

    cuts = _stamp_and_carve_cuts('slug', 'ep', remove, keep + remove, actions, keep)

    assert [(c['start'], c['end']) for c in cuts] == [(100.0, 130.0)]
    assert cuts[0]['action_applied'] == 'remove'


def test_pattern_defined_overrides_mark_to_remove():
    keep, remove = _partition_keep_ads(
        [_marker(pattern_defined=True)], {'sponsor': 'mark'})
    assert keep == []
    assert len(remove) == 1
    assert remove[0]['keep_overridden_by_pattern'] is True


def test_effective_resolved_action_overrides_mark_to_remove_for_pattern():
    marker = _marker(pattern_defined=True)
    assert effective_resolved_action(marker, {'sponsor': 'mark'}) == 'remove'


def test_effective_resolved_action_passes_mark_through_without_pattern():
    assert effective_resolved_action(_marker(), {'sponsor': 'mark'}) == 'mark'


def test_safety_net_catches_a_late_mark_resolution():
    ads_to_remove = [_marker()]
    result = _apply_late_keep_safety_net(
        ads_to_remove, ads_to_remove, {'sponsor': 'mark'})
    assert result == []
    assert ads_to_remove[0]['action_applied'] == 'mark'
    assert ads_to_remove[0]['was_cut'] is False


def test_safety_net_defined_pattern_still_cuts_a_mark_resolution():
    ads_to_remove = [_marker(pattern_defined=True)]
    result = _apply_late_keep_safety_net(
        ads_to_remove, ads_to_remove, {'sponsor': 'mark'})
    assert len(result) == 1
    assert result[0].get('keep_overridden_by_pattern') is True


# ---------- differential override: identical to keep (issue #728) ----------

DIFFERENTIAL = {'regions': [
    {'kind': 'differential', 'start_s': 10.0, 'end_s': 40.0, 'corr': 0.056}]}


def _override(enabled=True, corr_max=0.60, differential=DIFFERENTIAL):
    return KeepDifferentialOverride(differential, corr_max=corr_max, enabled=enabled)


def test_differential_override_cuts_an_injected_mark_marker():
    ad = _marker()
    keep, remove = _partition_keep_ads([ad], {'sponsor': 'mark'}, _override())
    assert keep == []
    assert remove == [ad]
    assert ad['keep_overridden_by_differential'] is True


def test_differential_override_leaves_an_organic_mark_marker_alone():
    ad = _marker(start=500.0, end=540.0)
    keep, remove = _partition_keep_ads([ad], {'sponsor': 'mark'}, _override())
    assert remove == []
    assert keep == [ad]
    assert ad['action_applied'] == 'mark'


def test_pass2_differential_override_treats_mark_like_keep():
    original = dict(_marker())
    processed = dict(_marker(start=60.0, end=90.0))
    rem_p, rem_o, kept_p, kept_o = _partition_pass2_category_actions(
        [processed], [original], {'sponsor': 'mark'}, _override())
    assert kept_o == [] and kept_p == []
    assert rem_o == [original]
    assert original['keep_overridden_by_differential'] is True


def test_pass2_organic_mark_marker_is_kept_and_stamped():
    original = dict(_marker(start=500.0, end=540.0))
    processed = dict(_marker(start=60.0, end=100.0))
    rem_p, rem_o, kept_p, kept_o = _partition_pass2_category_actions(
        [processed], [original], {'sponsor': 'mark'}, _override())
    assert kept_o == [original]
    assert rem_o == []
    assert original['action_applied'] == 'mark'
