"""A caller-supplied classifier_confirmed flag counts as ad evidence; a key in model output never does."""
import json
import logging
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from tests.app_bootstrap import bootstrap

bootstrap('classifier_confirmed_test_')

from ad_detector import AdDetector
from ad_detector.prompts import (_normalize_ad, format_window_prompt, parse_ads_from_response,
                                 parse_id_ads_from_response, resolve_segment_id_ads)
from systemone.adapter import run_chat_completion
from systemone.settings import SystemOneSettings
from sponsor_service import SponsorService
from tests.unit.test_systemone_adapter_detection import _fake, _sponsor_lookup

EXCERPT = 'Based on transcript: Our show today brought to you by a company, and use code X'
WINDOW = [{'sid': 1, 'start': 0.0, 'end': 60.0}, {'sid': 2, 'start': 60.0, 'end': 150.0}]


def _ad(**over):
    return {'start': 0.0, 'end': 150.0, 'confidence': 0.9, 'reason': EXCERPT,
            'end_text': 'use code X', **over}


def _id_ads(**over):
    raw = json.dumps({'ads': [{'start_id': 1, 'end_id': 2, 'confidence': 0.9, 'reason': EXCERPT,
                               'end_text': 'x', **over}]})
    ads, used = parse_id_ads_from_response(raw)
    assert used
    return ads


def test_classifier_span_over_threshold_is_kept():
    assert _normalize_ad(_ad(), 0.0, 150.0, classifier_confirmed=True) is not None


def test_same_span_without_the_parameter_is_rejected():
    assert _normalize_ad(_ad(), 0.0, 150.0) is None


@pytest.mark.parametrize('value', [True, 'true', 1])
def test_a_classifier_confirmed_key_in_the_ad_dict_has_no_effect(value):
    assert _normalize_ad(_ad(classifier_confirmed=value), 0.0, 150.0) is None
    assert parse_ads_from_response(json.dumps({'ads': [_ad(classifier_confirmed=value)]})) == []
    assert resolve_segment_id_ads(_id_ads(classifier_confirmed=value), WINDOW) == []


def test_low_confidence_classifier_span_is_still_rejected():
    assert _normalize_ad(_ad(confidence=0.3), 0.0, 80.0, classifier_confirmed=True) is None


def test_no_low_evidence_warning_for_a_classifier_span(caplog):
    with caplog.at_level(logging.WARNING):
        ad = _normalize_ad(_ad(), 0.0, 80.0, classifier_confirmed=True)
    assert ad is not None
    assert 'Low-confidence ad' not in caplog.text


def test_timestamp_parse_path_takes_the_parameter():
    raw = json.dumps({'ads': [_ad()]})
    assert parse_ads_from_response(raw) == []
    assert len(parse_ads_from_response(raw, classifier_confirmed=True)) == 1


def test_segment_id_parse_path_takes_the_parameter():
    ads = _id_ads()
    assert resolve_segment_id_ads(ads, WINDOW) == []
    assert len(resolve_segment_id_ads(ads, WINDOW, classifier_confirmed=True)) == 1


@pytest.mark.parametrize('mode,lines', [
    ('timestamps', ['[0.0-10.0] Sponsored by Unknown Brand, visit example.com today.']),
    ('segment_ids', ['[0] Sponsored by Unknown Brand, visit example.com today.']),
])
def test_adapter_output_carries_no_classifier_key(temp_db, mode, lines):
    prompt = format_window_prompt('Example Podcast', 'Episode', '', lines, 0, 1, 0.0, 600.0,
                                  addressing_mode=mode)
    result = run_chat_completion(
        messages=[{'role': 'user', 'content': prompt}], request_model='jev-latest',
        settings=SystemOneSettings(model='jev-latest', category_pass=False),
        fetcher=_fake({0}), sponsor_lookup=_sponsor_lookup(SponsorService(temp_db)))
    ads = json.loads(result['choices'][0]['message']['content'])['ads']
    assert ads and all('classifier_confirmed' not in ad for ad in ads)


def _window_ads(addressing_mode, diagnostics):
    """Run one 150 s window with no sponsor evidence, as answered by the given backend."""
    segs = [{'sid': 1, 'start': 0.0, 'end': 60.0, 'text': 'Our show today'},
            {'sid': 2, 'start': 60.0, 'end': 150.0, 'text': 'brought to you, use code X'}]
    ad = ({'start_id': 1, 'end_id': 2} if addressing_mode == 'segment_ids'
          else {'start': 0.0, 'end': 150.0})
    ad.update(confidence=0.9, reason=EXCERPT, end_text='use code X')
    response = SimpleNamespace(content=json.dumps({'ads': [ad]}), usage={}, diagnostics=diagnostics)
    detector = AdDetector(api_key='test-key')
    with patch.object(detector, '_call_llm_for_window', return_value=(response, None)):
        return detector._process_single_window(
            window_idx=0, window={'start': 0.0, 'end': 150.0, 'segments': segs},
            total_windows=1, model='m', system_prompt='x', description_section='',
            podcast_name='P', episode_title='E', audio_enforcer=None, audio_analysis=None,
            llm_timeout=30, max_retries=1, slug='feed', episode_id='ep', pass_name='pass1',
            window_label_prefix='Window', validate_timestamps=False,
            addressing_mode=addressing_mode).ads


@pytest.mark.parametrize('mode', ['timestamps', 'segment_ids'])
@pytest.mark.parametrize('diagnostics,kept', [
    ({'provider': 'systemone', 'outcome': 'ok'}, True),
    (None, False),
    ({'provider': 'other'}, False),
])
def test_detector_trusts_the_classifier_only_for_a_system_one_answer(mode, diagnostics, kept):
    assert bool(_window_ads(mode, diagnostics)) is kept
