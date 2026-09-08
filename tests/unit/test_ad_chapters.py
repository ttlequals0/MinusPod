"""Ad chapters: kept or held segments published as skippable chapters."""
from unittest.mock import MagicMock

from tests.app_bootstrap import bootstrap

bootstrap('ad_chapters_test_')

from ad_chapters import (  # noqa: E402
    AdChapterConfig, merge_ad_chapters, public_chapters,
    resolve_ad_chapter_config, strip_ad_chapters,
)

DURATION = 3600.0
CFG = AdChapterConfig(
    enabled=True,
    categories={'sponsor': True, 'cross_promo': True, 'self_promo': False,
                'interaction': False, 'intro': False, 'outro': True, 'recap': False},
    include_held=False, title_format='[mp:{category}]',
    held_title_format='[mp:{category}?]', resume_title='Show', min_confidence=0.9)


def topics():
    return [{'startTime': 1, 'title': 'Intro'},
            {'startTime': 600, 'title': 'Main topic'},
            {'startTime': 1800, 'title': 'Wrap up'}]


def kept(start, end, category='sponsor', **over):
    m = {'start': start, 'end': end, 'action_applied': 'keep',
         'category': category, 'confidence': 0.95, 'was_cut': False}
    m.update(over)
    return m


def held(start, end, category='sponsor'):
    return {'start': start, 'end': end, 'action_applied': 'remove',
            'category': category, 'held_for_review': True, 'was_cut': False,
            'confidence': 0.5}


def ads(result):
    return [ch for ch in result if ch.get('kind') == 'ad']


def test_disabled_config_strips_and_adds_nothing():
    stale = topics() + [{'startTime': 900, 'title': '[mp:sponsor]', 'kind': 'ad', 'category': 'sponsor'},
                        {'startTime': 960, 'title': 'Show', 'kind': 'resume'}]
    cfg = AdChapterConfig.disabled()
    assert merge_ad_chapters(stale, [kept(900.0, 960.0)], [], DURATION, 0.0, cfg) == topics()


def test_no_markers_returns_topics_unchanged():
    assert merge_ad_chapters(topics(), None, [], DURATION, 0.0, CFG) == topics()
    assert merge_ad_chapters(topics(), [], [], DURATION, 0.0, CFG) == topics()


def test_keep_marker_emits_ad_and_resume():
    result = merge_ad_chapters(topics(), [kept(900.0, 960.0)], [], DURATION, 0.0, CFG)
    assert {'startTime': 900, 'title': '[mp:sponsor]', 'kind': 'ad', 'category': 'sponsor'} in result
    assert {'startTime': 960, 'title': 'Show', 'kind': 'resume'} in result
    assert [c['startTime'] for c in result] == sorted(c['startTime'] for c in result)


def test_remove_and_beep_markers_are_ignored():
    markers = [kept(900.0, 960.0, action_applied='remove', was_cut=True),
               kept(1000.0, 1030.0, action_applied='beep', was_cut=True)]
    assert merge_ad_chapters(topics(), markers, [], DURATION, 0.0, CFG) == topics()


def test_unchecked_category_is_skipped():
    assert merge_ad_chapters(topics(), [kept(900.0, 960.0, 'self_promo')], [], DURATION, 0.0, CFG) == topics()


def test_confidence_floor_applies_to_kept_only():
    low = kept(900.0, 960.0, confidence=0.5)
    assert merge_ad_chapters(topics(), [low], [], DURATION, 0.0, CFG) == topics()
    adjusted = kept(900.0, 960.0, confidence=0.5,
                    validation={'adjusted_confidence': 0.95})
    assert ads(merge_ad_chapters(topics(), [adjusted], [], DURATION, 0.0, CFG))


def test_validated_downgrade_below_the_floor_drops_the_chapter():
    downgraded = kept(900.0, 960.0, confidence=0.95,
                      validation={'adjusted_confidence': 0.4})
    assert merge_ad_chapters(topics(), [downgraded], [], DURATION, 0.0, CFG) == topics()


def test_held_markers_only_when_included_and_use_held_title():
    assert merge_ad_chapters(topics(), [held(900.0, 960.0)], [], DURATION, 0.0, CFG) == topics()
    cfg = AdChapterConfig(**{**CFG.__dict__, 'include_held': True})
    result = merge_ad_chapters(topics(), [held(900.0, 960.0, 'cross_promo')], [], DURATION, 0.0, cfg)
    assert {'startTime': 900, 'title': '[mp:cross_promo?]', 'kind': 'ad',
            'category': 'cross_promo', 'held': True} in result


def test_marker_without_a_known_category_gets_no_chapter():
    cfg = AdChapterConfig(**{**CFG.__dict__, 'include_held': True})
    no_category = held(900.0, 960.0)
    no_category.pop('category')
    assert merge_ad_chapters(topics(), [no_category], [], DURATION, 0.0, cfg) == topics()
    unknown = held(900.0, 960.0, 'mystery')
    assert merge_ad_chapters(topics(), [unknown], [], DURATION, 0.0, cfg) == topics()


def test_held_marker_that_was_cut_is_not_pending():
    cfg = AdChapterConfig(**{**CFG.__dict__, 'include_held': True})
    m = held(900.0, 960.0)
    m['was_cut'] = True
    assert merge_ad_chapters(topics(), [m], [], DURATION, 0.0, cfg) == topics()


def test_topic_chapter_inside_span_is_hidden_not_deleted():
    result = merge_ad_chapters(topics(), [kept(500.0, 700.0)], [], DURATION, 0.0, CFG)
    assert {'startTime': 600, 'title': 'Main topic', 'hidden': True} in result
    assert 600 not in [c['startTime'] for c in public_chapters(result)]


def test_hidden_topic_returns_once_the_marker_is_gone():
    displaced = merge_ad_chapters(topics(), [kept(500.0, 700.0)], [], DURATION, 0.0, CFG)
    assert merge_ad_chapters(displaced, [], [], DURATION, 0.0, CFG) == topics()


def test_public_chapters_drops_hidden_entries_and_internal_keys():
    merged = merge_ad_chapters(topics(), [kept(500.0, 700.0)], [], DURATION, 0.0, CFG)
    served = public_chapters(merged)
    assert all(set(ch) == {'startTime', 'title'} for ch in served)
    assert [ch['startTime'] for ch in served] == [1, 500, 700, 1800]


def test_public_chapters_keeps_other_spec_keys():
    entries = [{'startTime': 1, 'title': 'Intro', 'img': 'https://example.com/a.png',
                'url': 'https://example.com'}]
    assert public_chapters(entries) == entries


def test_existing_chapter_within_snap_of_end_is_the_resume():
    result = merge_ad_chapters(topics(), [kept(400.0, 599.0)], [], DURATION, 0.0, CFG)
    assert [c for c in result if c.get('kind') == 'resume'] == []
    assert {'startTime': 600, 'title': 'Main topic'} in result


def test_ad_chapter_wins_over_snapped_topic_at_its_start():
    result = public_chapters(
        merge_ad_chapters(topics(), [kept(601.0, 700.0)], [], DURATION, 0.0, CFG))
    assert [c['title'] for c in result if c['startTime'] in (600, 601)] == ['[mp:sponsor]']


def test_overlapping_markers_merge_into_one_break():
    markers = [kept(900.0, 960.0), kept(950.0, 1010.0, 'cross_promo')]
    result = merge_ad_chapters(topics(), markers, [], DURATION, 0.0, CFG)
    assert ads(result) == [{'startTime': 900, 'title': '[mp:sponsor]', 'kind': 'ad', 'category': 'sponsor'}]
    assert {'startTime': 1010, 'title': 'Show', 'kind': 'resume'} in result


def test_span_running_to_episode_end_needs_no_resume():
    result = merge_ad_chapters(topics(), [kept(3500.0, 3600.0, 'outro')], [], DURATION, 0.0, CFG)
    assert result[-1] == {'startTime': 3500, 'title': '[mp:outro]', 'kind': 'ad', 'category': 'outro'}


def test_unknown_duration_still_adds_resume():
    result = merge_ad_chapters(topics(), [kept(3500.0, 3600.0, 'outro')], [], None, 0.0, CFG)
    assert result[-1] == {'startTime': 3600, 'title': 'Show', 'kind': 'resume'}


def test_markers_map_through_applied_cuts():
    cuts = [{'start': 100.0, 'end': 130.0}]  # 30s removed, 2s beep replaces it
    result = merge_ad_chapters([{'startTime': 1, 'title': 'Intro'}],
                               [kept(400.0, 420.0)], cuts, DURATION, 2.0, CFG)
    assert {'startTime': 372, 'title': '[mp:sponsor]', 'kind': 'ad', 'category': 'sponsor'} in result
    assert {'startTime': 392, 'title': 'Show', 'kind': 'resume'} in result


def test_stale_ad_entries_are_replaced_not_duplicated():
    stale = topics() + [{'startTime': 100, 'title': '[mp:sponsor]', 'kind': 'ad', 'category': 'sponsor'},
                        {'startTime': 160, 'title': 'Show', 'kind': 'resume'}]
    result = merge_ad_chapters(stale, [kept(900.0, 960.0)], [], DURATION, 0.0, CFG)
    assert [c['startTime'] for c in ads(result)] == [900]
    assert 160 not in [c['startTime'] for c in result]


def test_ad_only_list_when_no_topic_chapters():
    result = merge_ad_chapters([], [kept(900.0, 960.0)], [], DURATION, 0.0, CFG)
    assert [c['startTime'] for c in result] == [900, 960]


def test_zero_length_span_skipped():
    assert merge_ad_chapters(topics(), [kept(900.0, 900.0)], [], DURATION, 0.0, CFG) == topics()


def test_strip_removes_only_ad_kinds():
    mixed = topics() + [{'startTime': 5, 'title': 'x', 'kind': 'ad'},
                        {'startTime': 6, 'title': 'y', 'kind': 'resume'}]
    assert strip_ad_chapters(mixed) == topics()


def test_resolve_config_gates_on_chapters_mode_and_global_flags():
    db = MagicMock()
    db.get_setting_bool.side_effect = lambda key, default=False: {
        'chapters_enabled': True, 'ad_chapters_enabled': True,
        'ad_chapters_include_held': True}.get(key, default)
    db.get_setting.side_effect = lambda key: {
        'ad_chapter_title_format': '[a:{category}]',
        'ad_chapter_held_title_format': '[h:{category}]',
        'ad_chapter_resume_title': 'Back', 'ad_chapter_min_confidence': '0.7'}.get(key)
    db.resolve_ad_chapter_categories.return_value = {'sponsor': True}
    cfg = resolve_ad_chapter_config(db, {'chapters_mode': 'auto'}, slug='example-podcast')
    assert cfg.enabled and cfg.include_held
    assert (cfg.title_format, cfg.held_title_format, cfg.resume_title, cfg.min_confidence) == (
        '[a:{category}]', '[h:{category}]', 'Back', 0.7)
    assert cfg.categories == {'sponsor': True}
    assert not resolve_ad_chapter_config(db, {'chapters_mode': 'off'}).enabled
    assert not resolve_ad_chapter_config(db, {'ad_chapters_enabled_override': 'off'}).enabled
    db.get_setting_bool.side_effect = lambda key, default=False: key == 'ad_chapters_enabled'
    assert not resolve_ad_chapter_config(db, {}).enabled


def test_resolve_config_bad_confidence_uses_default():
    db = MagicMock()
    db.get_setting_bool.return_value = True
    db.get_setting.side_effect = lambda key: 'abc' if key == 'ad_chapter_min_confidence' else None
    db.resolve_ad_chapter_categories.return_value = {}
    assert resolve_ad_chapter_config(db, {}).min_confidence == 0.9


def test_unparseable_adjusted_confidence_falls_back_to_confidence():
    marker = kept(900.0, 960.0, validation={'adjusted_confidence': 'abc'},
                  confidence=0.1)
    assert merge_ad_chapters(topics(), [marker], [], DURATION, 0.0, CFG) == topics()


def test_keep_marker_with_hold_flag_is_never_treated_as_held():
    marker = kept(900.0, 960.0, held_for_review=True)
    result = merge_ad_chapters(topics(), [marker], [], DURATION, 0.0, CFG)
    assert {'startTime': 900, 'title': '[mp:sponsor]', 'kind': 'ad', 'category': 'sponsor'} in result
