"""Accepted reviewer abstentions must not reach either audio cut pass."""
from types import SimpleNamespace
from unittest.mock import MagicMock

from tests.app_bootstrap import bootstrap

bootstrap('processing_boundary_safety_test_')

from ad_reviewer import AdReviewer, split_resurrection_pool
from ad_detector.boundaries import _merge_ad_pair
from ad_validator import AdValidator, Decision, user_trimmed_keep_ranges
from audio_processor import AudioProcessor
from config import (HOLD_REASON_REVIEWER_INCONCLUSIVE_BOUNDS,
                    PASS2_AUTOAPPROVE_HOLD_REASONS, is_pending_review)
from main_app import processing
from main_app.verification_reconciliation import _gate_verification_ads_by_confidence
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
    pass2_cuts, pass2_ui, pass2_held, corroborated = (
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
