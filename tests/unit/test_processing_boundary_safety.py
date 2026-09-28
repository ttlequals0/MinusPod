"""Accepted reviewer abstentions must not reach either audio cut pass."""
from types import SimpleNamespace

import pytest
from unittest.mock import MagicMock

from tests.app_bootstrap import bootstrap

bootstrap('processing_boundary_safety_test_')

from ad_detector import AdDetector
from ad_reviewer import AdReviewer, split_resurrection_pool
from ad_detector.boundaries import (_merge_ad_pair, effective_resolved_action,
                                   split_conflicting_action_span)
from ad_validator import AdValidator, Decision, user_trimmed_keep_ranges
from audio_processor import AudioProcessor
from config import (HOLD_REASON_REVIEWER_INCONCLUSIVE_BOUNDS,
                    PASS2_AUTOAPPROVE_HOLD_REASONS, is_pending_review)
from main_app import processing
from main_app.verification_reconciliation import _gate_verification_ads_by_confidence
from tests.unit.test_ad_reviewer import _build_reviewer, _mock_episode_meta, _resp
from tests.unit.test_keep_bypass import _run_pipeline


def test_saved_trim_protects_audio_inside_longer_new_detection():
    corrections = [{
        'start': 100.0, 'end': 160.0,
        'confirmed_span': {'start': 101.7, 'end': 160.0},
        'correction_type': 'confirm',
    }]
    validator = AdValidator(
        episode_duration=600.0, segments=[],
        confirmed_corrections=corrections,
    )
    detected = {
        'start': 100.0, 'end': 290.0, 'confidence': 0.98,
        'reason': 'Host-read sponsor offer with a discount code',
        'detection_stage': 'claude',
        'dai_core_spans': [{'start': 100.0, 'end': 290.0}],
        '_saved_was_cut': True,
    }
    result = validator.validate([detected])
    kept = [ad for ad in result.ads if ad.get('_user_kept_by_trim')]
    assert [(ad['start'], ad['end']) for ad in kept] == [(100.0, 101.7)]
    assert kept[0]['validation']['decision'] == Decision.REJECT.value
    assert kept[0]['_skip_pattern_learning']
    assert split_resurrection_pool(result.ads, [], 0.1) == []
    accepted = [ad for ad in result.ads
                if ad['validation']['decision'] == Decision.ACCEPT.value]
    assert accepted and accepted[0]['start'] >= 101.7
    assert max(ad['end'] for ad in accepted) == 290.0

    accepted[0]['start'] = 100.0
    ranges = user_trimmed_keep_ranges(corrections)
    final = processing._protect_user_trimmed_cuts(accepted, result.ads, ranges)
    applied = AudioProcessor().compute_applied_cuts(
        final, 600.0, cut_barriers=ranges)
    assert applied[0]['start'] == 101.7
    assert applied[-1]['end'] == 290.0
    assert all(not (cut['start'] < 101.7 and cut['end'] > 100.0)
               for cut in applied)


def test_verification_keeps_trim_but_checks_remaining_ad():
    processed = [{'start': 100.0, 'end': 290.0, 'confidence': 0.98,
                  'dai_core_spans': [{'start': 100.0, 'end': 290.0}]}]
    original = [dict(processed[0])]
    kept = [{'start': 100.0, 'end': 101.7}]
    remaining, mapped = processing._split_pass2_candidates_around_spans(
        processed, original, kept, [], 'user-trimmed audio')
    assert [(ad['start'], ad['end']) for ad in remaining] == [
        (101.7, 290.0)]
    assert [(ad['start'], ad['end']) for ad in mapped] == [
        (101.7, 290.0)]
    assert remaining[0]['dai_core_spans'][0]['start'] == 101.7
    assert mapped[0]['dai_core_spans'][0]['start'] == 101.7
    assert remaining[0]['_measured_split_fragment']


class InconclusiveError(Exception):
    status_code = 422
    body = {'error': {'code': 'jev_review_inconclusive',
                      'reason': 'missing_boundary_coverage',
                      'stage': 'boundary_coverage'}}


def _reviewer():
    db = MagicMock()
    db.get_setting.side_effect = lambda key: {
        'review_prompt': 'review', 'resurrect_prompt': 'resurrect',
    }.get(key)
    return AdReviewer(db=db, llm_client=MagicMock())


def _meta():
    return {'podcast_name': 'Example Podcast', 'episode_title': 'Episode',
            'podcast_description': '', 'episode_description': '',
            'slug': 'example-podcast', 'episode_id': 'episode-1',
            'podcast_id': 1}


def test_abstained_pass1_and_adjacent_pass2_never_reach_render_or_learning(monkeypatch):
    segments = [
        {'start': 410.0, 'end': 450.0, 'text': 'Show discussion.'},
        {'start': 450.0, 'end': 600.0, 'text': 'Sponsored by Acme.'},
        {'start': 600.0, 'end': 680.0, 'text': 'Show discussion resumes.'},
    ]
    candidate = {'start': 420.0, 'end': 625.0, 'confidence': 0.98,
                 'detection_stage': 'claude', 'sponsor': 'Acme',
                 'reason': 'Acme sponsor read'}
    validation = AdValidator(
        1000.0, segments, episode_description='Acme sponsors this episode',
        splice_veto_enabled=False).validate([candidate])
    cuts, _ = processing._gate_validation_by_confidence(
        'example-podcast', 'episode-1', validation.ads, 0.80)
    assert len(cuts) == 1

    reviewer = _reviewer()
    monkeypatch.setattr(processing, '_ad_review_enabled', lambda db: True)
    monkeypatch.setattr(processing, '_build_reviewer', lambda db, detector: reviewer)
    monkeypatch.setattr(processing, 'clear_fallback', lambda *args: None)
    monkeypatch.setattr(processing, '_publish_status', lambda *args: None)
    monkeypatch.setattr(processing.storage, 'save_combined_ads', lambda *args: None)
    monkeypatch.setattr('ad_reviewer.call_llm_for_window',
                        lambda **kwargs: (None, InconclusiveError()))

    cuts, markers = processing._run_ad_reviewer(
        'example-podcast', 'episode-1', 1, cuts, validation.ads, segments,
        'Example Podcast', 'Episode', '', '', 0.80, 1, 'test-model')
    assert cuts == []
    assert len(markers) == 1
    assert is_pending_review(markers[0])
    assert markers[0]['hold_reason'] == HOLD_REASON_REVIEWER_INCONCLUSIVE_BOUNDS

    original = {'start': 625.8, 'end': 677.1, 'confidence': 0.98,
                'detection_stage': 'claude'}
    processed = dict(original, validation={'decision': 'ACCEPT',
                                           'adjusted_confidence': 0.98})
    pass2_cuts, pass2_ui, pass2_held, corroborated, _rel = (
        _gate_verification_ads_by_confidence(
            [processed], [original], 0.80, pass1_held_markers=markers))
    assert len(pass2_cuts) == 1
    assert corroborated == 0
    context = SimpleNamespace(
        slug='example-podcast', episode_id='episode-1', podcast_id=1,
        podcast_name='Example Podcast', episode_title='Episode',
        podcast_description='', episode_description='')
    monkeypatch.setattr(processing.ad_detector, 'get_verification_model',
                        lambda: 'test-model')
    processing._apply_pass2_reviewer(
        context, pass2_cuts, pass2_ui, pass2_held,
        [processed], [original], segments, 0.80, pass1_cuts=[])
    assert pass2_cuts == []
    assert pass2_ui == []
    assert pass2_held == [original]
    assert is_pending_review(original)
    assert original['hold_reason'] == HOLD_REASON_REVIEWER_INCONCLUSIVE_BOUNDS
    assert original['start'] == 625.8 and original['end'] == 677.1
    assert HOLD_REASON_REVIEWER_INCONCLUSIVE_BOUNDS not in PASS2_AUTOAPPROVE_HOLD_REASONS

    learn = MagicMock()
    monkeypatch.setattr(processing.ad_detector, 'learn_from_detections', learn)
    assert processing._learn_from_applied_cut_ads(
        'example-podcast', 'episode-1', cuts, markers,
        [], 1000.0, segments, 'unused.wav') == 0
    learn.assert_not_called()


def test_abstained_merged_fingerprint_cannot_certify_estimated_tail(monkeypatch):
    candidate = {
        'start': 0.0, 'end': 175.5, 'confidence': 1.0,
        'detection_stage': 'fingerprint', 'pattern_id': 7,
        'merged_distinct_ads': True,
        'merged_member_spans': [
            {'start': 0.0, 'end': 60.28, 'stage': 'fingerprint'},
            {'start': 60.28, 'end': 175.5, 'stage': 'text_pattern'},
        ],
        'span_estimated': True, 'text_start': 60.28, 'text_end': 94.42,
        'validation': {'decision': 'ACCEPT', 'sponsor_confirmed': True},
    }
    reviewer = _reviewer()
    reviewer.db.get_ad_pattern_by_id.return_value = {
        'created_by': 'user', 'is_active': 1}
    monkeypatch.setattr('ad_reviewer.call_llm_for_window',
                        lambda **kwargs: (None, InconclusiveError()))
    result = reviewer.review([candidate], [], [], _meta(), 1, 'test-model')
    assert result.accepted_after_review == []
    assert result.held_by_inconclusive[0]['end'] == 175.5


def test_abstained_partial_dai_holds_but_full_dai_cuts(monkeypatch):
    reviewer = _reviewer()
    monkeypatch.setattr('ad_reviewer.call_llm_for_window',
                        lambda **kwargs: (None, InconclusiveError()))
    partial = {'start': 2279.5, 'end': 2393.3, 'confidence': 0.98,
               'detection_stage': 'dai_differential',
               'dai_core_spans': [{'start': 2320.22, 'end': 2393.3}]}
    full = {'start': 0.0, 'end': 60.0, 'confidence': 0.98,
            'detection_stage': 'dai_differential',
            'dai_core_spans': [{'start': 0.0, 'end': 60.0}]}
    result = reviewer.review([partial, full], [], [], _meta(), 1, 'test-model')
    assert result.accepted_after_review == [full]
    assert result.held_by_inconclusive[0]['start'] == 2279.5


def test_abstained_fingerprint_needs_defined_pattern_and_original_bounds(monkeypatch):
    reviewer = _reviewer()
    monkeypatch.setattr('ad_reviewer.call_llm_for_window',
                        lambda **kwargs: (None, InconclusiveError()))
    base = {'start': 10.0, 'end': 70.0, 'confidence': 0.95,
            'detection_stage': 'fingerprint', 'pattern_id': 7,
            'fingerprint_match_start': 10.0, 'fingerprint_match_end': 70.0}
    expanded = dict(base, end=90.0)
    reviewer.db.get_ad_pattern_by_id.return_value = {
        'created_by': 'user', 'is_active': 1}
    result = reviewer.review([base, expanded], [], [], _meta(), 1, 'test-model')
    assert result.accepted_after_review == [base]
    assert result.held_by_inconclusive[0]['end'] == 90.0

    reviewer.db.get_ad_pattern_by_id.return_value = {
        'created_by': 'auto', 'is_active': 1}
    result = reviewer.review([base], [], [], _meta(), 1, 'test-model')
    assert result.accepted_after_review == []


def test_abstained_fingerprint_with_absorbed_llm_member_still_cuts(monkeypatch):
    reviewer = _reviewer()
    monkeypatch.setattr('ad_reviewer.call_llm_for_window',
                        lambda **kwargs: (None, InconclusiveError()))
    reviewer.db.get_ad_pattern_by_id.return_value = {
        'created_by': 'user', 'is_active': 1}
    absorbed = {'start': 10.0, 'end': 70.0, 'confidence': 0.95,
                'detection_stage': 'fingerprint', 'pattern_id': 7,
                'fingerprint_match_start': 10.0, 'fingerprint_match_end': 70.0,
                'merged_protected_start': 10.0, 'merged_protected_end': 70.0,
                'merged_member_spans': [
                    {'start': 10.0, 'end': 70.0, 'stage': 'fingerprint'},
                    {'start': 15.0, 'end': 68.0, 'stage': 'claude',
                     'confidence': 0.9}]}
    past_match = dict(absorbed, merged_protected_end=90.0)
    result = reviewer.review([absorbed, past_match], [], [], _meta(), 1, 'test-model')
    assert result.accepted_after_review == [absorbed]
    assert [m['merged_protected_end'] for m in result.held_by_inconclusive] == [90.0]


def test_full_processing_pass_renders_no_abstained_cut(monkeypatch):
    reviewer = _reviewer()
    monkeypatch.setattr(processing, '_build_reviewer', lambda db, detector: reviewer)
    monkeypatch.setattr('ad_reviewer.call_llm_for_window',
                        lambda **kwargs: (None, InconclusiveError()))
    candidate = {'start': 20.0, 'end': 60.0, 'confidence': 0.98,
                 'detection_stage': 'claude', 'sponsor': 'Acme',
                 'reason': 'Acme sponsor read', 'category': 'sponsor'}
    segments = [
        {'start': 0.0, 'end': 20.0, 'text': 'Opening discussion.'},
        {'start': 20.0, 'end': 60.0, 'text': 'A message from Acme.'},
        {'start': 60.0, 'end': 100.0, 'text': 'Discussion continues.'},
    ]
    run = _run_pipeline(
        [candidate], {'sponsor': 'remove'}, segments=segments,
        real_refine_reviewer=True)
    assert run['result'] is True
    assert run['local_ap'].process_episode.call_args.args[1] == []
    assert any(any(is_pending_review(marker) for marker in call.args[2])
               for call in run['storage'].save_combined_ads.call_args_list)


def test_boundary_merge_keeps_estimated_pattern_risk():
    measured = {'start': 0.0, 'end': 60.0, 'confidence': 0.95,
                'detection_stage': 'fingerprint'}
    estimated = {'start': 60.0, 'end': 175.0, 'confidence': 0.9,
                 'detection_stage': 'text_pattern',
                 'has_estimated_pattern_member': True}
    _merge_ad_pair(measured, estimated)
    assert measured['has_estimated_pattern_member'] is True
    assert measured['end'] == 175.0


KEEP_PROMO_MAP = {'sponsor': 'remove', 'interaction': 'remove', 'cross_promo': 'keep',
                  'self_promo': 'keep', 'intro': 'keep', 'outro': 'keep', 'recap': 'keep'}
REMOVE_PROMO_MAP = {category: 'remove' for category in KEEP_PROMO_MAP}
PROMO_KEEP = (2588.74, 2617.19)
PROMO_TEXT_END = 2628.74
READ_END = 2707.94


def _promo_members(precise_read=True, text_defined=False):
    """Fingerprint and estimated text self-promo inside a longer sponsor read."""
    fingerprint = {'start': 2588.74, 'end': 2617.19, 'confidence': 0.99,
                   'detection_stage': 'fingerprint', 'category': 'self_promo',
                   'pattern_id': 12, 'pattern_defined': False,
                   'fingerprint_match_start': 2588.74, 'fingerprint_match_end': 2617.19,
                   'reason': 'Show promo fingerprint'}
    text = {'start': 2595.7, 'end': 2675.0, 'confidence': 0.9,
            'detection_stage': 'text_pattern', 'category': 'self_promo',
            'pattern_id': 13, 'pattern_defined': text_defined, 'span_estimated': True,
            'text_start': 2595.7, 'text_end': PROMO_TEXT_END, 'reason': 'Show promo text'}
    read = {'start': 2593.03, 'end': READ_END, 'confidence': 0.96,
            'detection_stage': 'claude', 'category': 'sponsor', 'sponsor': 'Acme',
            'reason': 'Acme sponsor read'}
    if precise_read:
        read.update(word_timed_start=2593.03, word_timed_end=READ_END)
    return [fingerprint, text, read]


def _merge(ads, action_map):
    return AdDetector.__new__(AdDetector)._merge_detection_results(
        ads, None, action_map=action_map)


def _spans(ads):
    return [(round(a['start'], 2), round(a['end'], 2), a.get('category')) for a in ads]


def _render(merged, action_map, duration=2708.2445):
    keeps = [a for a in merged if action_map.get(a.get('category')) == 'keep']
    cuts = [dict(a) for a in merged if a not in keeps]
    cuts = processing._carve_cuts_around_kept_audio('example-podcast', 'a1b2c3d4e5f6',
                                                    cuts, list(merged), keeps)
    return AudioProcessor().compute_applied_cuts(cuts, duration, hard_barriers=keeps)


def test_keep_map_leaves_promo_audio_and_cuts_the_read_after_it():
    merged = _merge(_promo_members(), KEEP_PROMO_MAP)

    assert _spans(merged) == [(*PROMO_KEEP, 'self_promo'),
                              (2595.7, PROMO_TEXT_END, 'self_promo'),
                              (PROMO_TEXT_END, READ_END, 'sponsor')]
    applied = _render(merged, KEEP_PROMO_MAP)
    assert [(c['start'], c['end']) for c in applied] == [(PROMO_TEXT_END, READ_END)]


def test_remove_map_merges_promo_and_read_into_one_cut():
    merged = _merge(_promo_members(), REMOVE_PROMO_MAP)

    assert [(round(a['start'], 2), round(a['end'], 2)) for a in merged] == [
        (PROMO_KEEP[0], READ_END)]


def test_estimated_keep_without_a_precise_read_stays_whole():
    merged = _merge(_promo_members(precise_read=False), KEEP_PROMO_MAP)

    assert (2595.7, 2675.0, 'self_promo') in _spans(merged)


def test_defined_promo_pattern_is_never_clipped_to_its_text():
    merged = _merge(_promo_members(text_defined=True), KEEP_PROMO_MAP)

    assert [(round(a['start'], 2), round(a['end'], 2),
             effective_resolved_action(a, KEEP_PROMO_MAP)) for a in merged] == [
        (*PROMO_KEEP, 'keep'), (PROMO_KEEP[1], READ_END, 'remove')]


def _review_read_start(proposed_start, original, core_start):
    reviewer = _build_reviewer({'review_prompt': 'review', 'resurrect_prompt': 'resurrect',
                                'review_max_boundary_shift': '60'})
    reviewer._llm_client.messages_create.return_value = _resp(
        f'[{{"start": {proposed_start}, "end": {original[1]}, "confidence": 0.96}}]')
    read = {'start': original[0], 'end': original[1], 'confidence': 0.97,
            'detection_stage': 'dai_differential', 'category': 'sponsor',
            'dai_core_spans': [{'start': core_start, 'end': original[1]}],
            'merged_member_spans': [
                {'start': original[0], 'end': original[1], 'stage': 'claude',
                 'confidence': 0.97, 'precise_start': False, 'precise_end': True}]}
    segments = [
        {'start': original[0] - 40.0, 'end': original[0], 'text': 'Show promo read.'},
        {'start': original[0], 'end': original[1], 'text': 'A message from Acme.'},
    ]
    meta = dict(_mock_episode_meta(), hard_barriers=[
        {'start': original[0] - 28.45, 'end': original[0]}])
    result = reviewer.review(accepted_ads=[read], resurrection_eligible=[], segments=segments,
                             episode_meta=meta, pass_num=1, pass_model='claude-test')
    return result.accepted_after_review[0], meta['hard_barriers']


def test_reviewer_adjust_into_kept_promo_is_clamped_at_the_keep_edge():
    out, keeps = _review_read_start(2609.36, (PROMO_KEEP[1], 2708.2445), 2647.12)

    assert out['start'] == PROMO_KEEP[1]
    applied = AudioProcessor().compute_applied_cuts([out], 2708.2445, hard_barriers=keeps)
    assert applied and applied[0]['start'] >= PROMO_KEEP[1]


def test_reviewer_inward_trim_after_kept_promo_still_cuts():
    out, keeps = _review_read_start(2141.21, (2128.45, 2187.36), 2141.21)

    assert (out['start'], out['end']) == (2141.21, 2187.36)
    applied = AudioProcessor().compute_applied_cuts([out], 2400.0, hard_barriers=keeps)
    assert [(c['start'], c['end']) for c in applied] == [(2141.21, 2187.36)]


SHORT_KEEP_MAP = {'sponsor': 'remove', 'self_promo': 'keep'}


def _precise_read(start, end):
    return {'start': start, 'end': end, 'confidence': 0.96, 'detection_stage': 'claude',
            'category': 'sponsor', 'reason': 'Acme sponsor read',
            'word_timed_start': start, 'word_timed_end': end}


def _estimated_promo(start, end, text_start, text_end):
    return {'start': start, 'end': end, 'confidence': 0.9, 'detection_stage': 'text_pattern',
            'category': 'self_promo', 'pattern_defined': False, 'span_estimated': True,
            'text_start': text_start, 'text_end': text_end, 'reason': 'Show promo text'}


@pytest.mark.parametrize('read_end,expected', [
    (200.0, [(120.0, 170.0, 'sponsor'), (170.0, 180.0, 'self_promo'),
             (180.0, 200.0, 'sponsor')]),
    (175.0, [(120.0, 170.0, 'sponsor'), (170.0, 180.0, 'self_promo')]),
])
def test_leading_estimate_keeps_the_read_before_the_matched_text(read_end, expected):
    merged = _merge([_estimated_promo(100.0, 180.0, 170.0, 180.0),
                     _precise_read(120.0, read_end)], SHORT_KEEP_MAP)

    assert _spans(merged) == expected


@pytest.mark.parametrize('stage', ['claude', 'cue_pair'])
def test_measured_keep_member_is_never_clipped_to_the_text(stage):
    detected = {'start': 100.0, 'end': 160.0, 'confidence': 0.9, 'detection_stage': stage,
                'category': 'self_promo', 'reason': 'Show promo'}
    merged = _merge([detected, _estimated_promo(101.0, 180.0, 101.0, 120.0),
                     _precise_read(150.0, 250.0)], SHORT_KEEP_MAP)

    assert _spans(merged) == [(100.0, 160.0, 'self_promo'), (160.0, 250.0, 'sponsor')]


def test_estimated_keep_without_text_bounds_is_logged_and_left_whole(caplog):
    promo = _estimated_promo(100.0, 180.0, None, None)
    with caplog.at_level('INFO'):
        merged = _merge([promo, _precise_read(150.0, 250.0)], SHORT_KEEP_MAP)

    assert (100.0, 180.0, 'self_promo') in _spans(merged)
    assert any('no matched text bounds' in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize('keep_first', [True, False])
def test_disjoint_after_clip_both_survive_in_start_order(keep_first):
    promo, read = _estimated_promo(100.0, 180.0, 100.0, 110.0), _precise_read(150.0, 250.0)
    last, current = (promo, read) if keep_first else (read, promo)
    new_last, entries = split_conflicting_action_span(
        last, current, SHORT_KEEP_MAP[last['category']], SHORT_KEEP_MAP[current['category']])

    out = ([new_last] if new_last else []) + entries
    assert [(a['start'], a['end']) for a in out] == [(100.0, 110.0), (150.0, 250.0)]
    assert next(a for a in out if a['category'] == 'sponsor') == read
