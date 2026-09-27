"""A failed review holds an unsupported cut instead of cutting it unreviewed."""
import logging
from types import SimpleNamespace

import httpx
import openai

from tests.app_bootstrap import bootstrap

bootstrap('reviewer_failure_hold_test_')

from config import (HOLD_REASON_REVIEWER_FAILED, PASS2_AUTOAPPROVE_HOLD_REASONS,
                    PASS2_COVERAGE_ONLY_HOLD_REASONS, is_pending_review)
from main_app import processing
from main_app.verification_reconciliation import _gate_verification_ads_by_confidence
from tests.unit.test_keep_bypass import _run_pipeline
from tests.unit.test_processing_boundary_safety import _meta, _reviewer

SUPPORTED_FLAG = 'INFO: Reviewer failed; bounds supported'


def _proxy_422():
    request = httpx.Request('POST', 'http://example.com/v1/chat/completions')
    return openai.UnprocessableEntityError(
        'Error code: 422', response=httpx.Response(422, request=request),
        body={'error': {'message': 'Unprocessable request',
                        'type': 'invalid_request_error'}})


def _fail_review(monkeypatch):
    monkeypatch.setattr('ad_reviewer.call_llm_for_window',
                        lambda **kwargs: (None, _proxy_422()))


def _review(reviewer, ad, pass_num=1):
    return reviewer.review(
        accepted_ads=[ad], resurrection_eligible=[], segments=[],
        episode_meta=_meta(), pass_num=pass_num, pass_model='test-model')


def test_failure_without_support_is_held(monkeypatch, caplog):
    _fail_review(monkeypatch)
    ad = {'start': 3544.2, 'end': 3680.7, 'confidence': 0.95,
          'detection_stage': 'claude'}
    with caplog.at_level(logging.INFO, logger='ad_reviewer'):
        result = _review(_reviewer(), ad)
    assert result.accepted_after_review == []
    assert result.verdicts[0].verdict == 'failure'
    assert result.verdicts[0].inconclusive_hold is True
    held = result.held_by_inconclusive[0]
    assert held['hold_reason'] == HOLD_REASON_REVIEWER_FAILED
    assert held['was_cut'] is False and is_pending_review(held)
    assert len([r for r in caplog.records if r.levelno == logging.INFO
                and 'Reviewer unavailable; bounds unsupported' in r.getMessage()]) == 1


def test_failure_with_full_dai_core_is_accepted_with_flag(monkeypatch):
    _fail_review(monkeypatch)
    ad = {'start': 3544.2, 'end': 3680.7, 'confidence': 0.95,
          'detection_stage': 'dai_differential',
          'dai_core_spans': [{'start': 3544.2, 'end': 3680.7}]}
    result = _review(_reviewer(), ad)
    assert result.accepted_after_review == [ad]
    assert result.held_by_inconclusive == []
    assert result.verdicts[0].inconclusive_hold is False
    assert ad['validation']['flags'] == [SUPPORTED_FLAG]


def test_failure_with_fingerprint_cover_is_accepted_with_flag(monkeypatch):
    _fail_review(monkeypatch)
    reviewer = _reviewer()
    reviewer.db.get_ad_pattern_by_id.return_value = {
        'is_active': True, 'created_by': 'user'}
    ad = {'start': 3492.9, 'end': 3544.2, 'confidence': 0.95,
          'detection_stage': 'fingerprint', 'pattern_id': 7,
          'fingerprint_match_start': 3492.9, 'fingerprint_match_end': 3544.2,
          'validation': {'flags': ['INFO: existing']}}
    result = _review(reviewer, ad)
    assert result.accepted_after_review == [ad]
    assert ad['validation']['flags'] == ['INFO: existing', SUPPORTED_FLAG]


def test_pass2_verification_marker_with_422_is_held(monkeypatch):
    _fail_review(monkeypatch)
    reviewer = _reviewer()
    monkeypatch.setattr(processing, '_ad_review_enabled', lambda db: True)
    monkeypatch.setattr(processing, '_build_reviewer', lambda db, detector: reviewer)
    monkeypatch.setattr(processing, 'clear_fallback', lambda *args: None)
    monkeypatch.setattr(processing.ad_detector, 'get_verification_model',
                        lambda: 'test-model')
    original = {'start': 3544.2, 'end': 3680.7, 'confidence': 0.98,
                'detection_stage': 'claude'}
    processed = dict(original, validation={'decision': 'ACCEPT',
                                           'adjusted_confidence': 0.98})
    cuts, ui, held, _ = _gate_verification_ads_by_confidence(
        [processed], [original], 0.80, pass1_held_markers=[])
    assert len(cuts) == 1
    context = SimpleNamespace(
        slug='example-podcast', episode_id='a1b2c3d4e5f6', podcast_id=1,
        podcast_name='Example Podcast', episode_title='Episode',
        podcast_description='', episode_description='')
    processing._apply_pass2_reviewer(
        context, cuts, ui, held, [processed], [original], [], 0.80,
        pass1_cuts=[])
    assert cuts == []
    assert held == [original]
    assert original['hold_reason'] == HOLD_REASON_REVIEWER_FAILED
    assert is_pending_review(original)
    assert HOLD_REASON_REVIEWER_FAILED not in PASS2_AUTOAPPROVE_HOLD_REASONS
    assert HOLD_REASON_REVIEWER_FAILED not in PASS2_COVERAGE_ONLY_HOLD_REASONS


def test_pipeline_does_not_render_a_failed_review(monkeypatch):
    reviewer = _reviewer()
    monkeypatch.setattr(processing, '_build_reviewer', lambda db, detector: reviewer)
    _fail_review(monkeypatch)
    candidate = {'start': 3544.2, 'end': 3573.2, 'confidence': 0.98,
                 'detection_stage': 'claude', 'sponsor': 'Acme',
                 'reason': 'Acme sponsor read', 'category': 'sponsor'}
    segments = [
        {'start': 3492.9, 'end': 3544.2, 'text': 'Opening discussion.'},
        {'start': 3544.2, 'end': 3573.2, 'text': 'A message from Acme.'},
        {'start': 3573.2, 'end': 3680.7, 'text': 'Discussion continues.'},
    ]
    run = _run_pipeline([candidate], {'sponsor': 'remove'}, segments=segments,
                        real_refine_reviewer=True, duration=3700.0)
    assert run['result'] is True
    assert run['local_ap'].process_episode.call_args.args[1] == []
    saved = run['storage'].save_combined_ads.call_args_list[-1].args[2]
    marker = next(m for m in saved if m.get('sponsor') == 'Acme')
    assert marker['hold_reason'] == HOLD_REASON_REVIEWER_FAILED
    assert marker['was_cut'] is False
