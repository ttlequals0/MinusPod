"""Exact ad quote boundaries in mixed transcript segments."""
import json
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from tests.app_bootstrap import bootstrap

bootstrap('ad_quote_alignment_test_')

from ad_detector.boundaries import (align_ad_quote_bounds,
                                    deduplicate_window_ads,
                                    timed_line_segments)
from ad_detector import AdDetector
from ad_detector.prompts import (parse_ads_from_response,
                                 parse_id_ads_from_response,
                                 resolve_segment_id_ads)
from ad_reviewer import AdReviewer, inconclusive_bounds_supported
from ad_validator import AdValidator
from main_app import processing
from text_pattern_matcher import TextMatch
from tests.unit.test_keep_bypass import _run_pipeline


WORDS = [
    (100, 'Today'), (101, 'we'), (102, 'discuss'), (103, 'updates.'),
    (106, 'A'), (107, 'word'), (108, 'from'), (109, 'our'),
    (110, 'sponsor'), (111, 'Acme.'), (130, 'Try'), (131, 'the'),
    (132, 'product'), (133, 'at'), (134, 'acme.com.'),
    (182, 'Thanks'), (183, 'to'), (184, 'Acme'), (185, 'for'),
    (186, 'supporting'), (187, 'us.'),
    (189, 'Back'), (190, 'to'), (191, 'the'), (192, 'story.'),
]


def _segments(words=WORDS):
    return [{
        'start': 100.0, 'end': 200.0,
        'text': ' '.join(word for _, word in words),
        'words': [{'start': float(time), 'end': time + 0.8, 'word': word}
                  for time, word in words],
    }]


def _candidate(**changes):
    ad = {
        'start': 100.0, 'end': 200.0, 'confidence': 0.95,
        'reason': 'Sponsor read', 'sponsor': 'Acme',
        'start_text': 'A word from our sponsor Acme',
        'end_text': 'Thanks to Acme for supporting us',
    }
    ad.update(changes)
    return ad


@dataclass
class _Response:
    content: str
    model: str = 'test-model'


def test_ad_only_quotes_reach_confirmed_cut_without_show_words():
    ad = align_ad_quote_bounds([_candidate()], _segments())[0]
    assert (ad['start'], ad['end']) == (106.0, 187.8)
    assert ad['quote_aligned_start'] and ad['quote_aligned_end']
    assert not inconclusive_bounds_supported(ad, None)

    db = MagicMock()
    db.get_setting.side_effect = lambda key: {
        'review_prompt': 'review', 'resurrect_prompt': 'resurrect',
        'review_max_boundary_shift': '60',
    }.get(key)
    reviewer = AdReviewer(db=db, llm_client=MagicMock())
    reviewer._llm_client.messages_create.return_value = _Response(
        '[{"start": 106.0, "end": 187.8, "confidence": 0.95, '
        '"reason": "Confirmed sponsor read"}]')
    result = reviewer.review(
        accepted_ads=[ad], resurrection_eligible=[], segments=_segments(),
        episode_meta={
            'podcast_name': 'Example Podcast', 'episode_title': 'Episode',
            'podcast_description': '', 'episode_description': '',
            'slug': 'example-podcast', 'episode_id': 'episode-1',
            'podcast_id': 1,
        }, pass_num=1, pass_model='test-model')
    assert len(result.accepted_after_review) == 1
    cut = result.accepted_after_review[0]
    assert (cut['start'], cut['end']) == (106.0, 187.8)
    retained = [word for time, word in WORDS
                if not (cut['start'] <= time < cut['end'])]
    assert retained == ['Today', 'we', 'discuss', 'updates.',
                        'Back', 'to', 'the', 'story.']


def test_exact_show_quotes_do_not_override_reviewer_abstention():
    ad = align_ad_quote_bounds([_candidate(
        start_text='Today we discuss updates',
        end_text='Back to the story')], _segments())[0]
    assert (ad['start'], ad['end']) == (100.0, 192.8)
    assert not inconclusive_bounds_supported(ad, None)


def test_repeated_quote_and_outward_edges_do_not_align():
    duplicate = _segments(WORDS + [(195, 'A'), (196, 'word'), (197, 'from'),
                                  (198, 'our'), (199, 'sponsor'),
                                  (199.1, 'Acme.')])
    assert 'quote_aligned_start' not in align_ad_quote_bounds(
        [_candidate()], duplicate)[0]
    assert 'quote_aligned_start' not in align_ad_quote_bounds(
        [_candidate(start=107.0)], _segments())[0]


def test_unrelated_timing_overlap_does_not_hide_clean_quote_edges():
    words = list(WORDS)
    words[12] = (131.4, 'product')
    ad = align_ad_quote_bounds([_candidate()], _segments(words))[0]
    assert (ad['start'], ad['end']) == (106.0, 187.8)


def test_overlapping_coarse_proposal_cannot_retain_stale_quote_flags():
    precise = align_ad_quote_bounds([_candidate()], _segments())[0]
    coarse = _candidate(start_text='', end_text='')
    merged = deduplicate_window_ads([coarse, precise])[0]
    assert (merged['start'], merged['end']) == (100.0, 200.0)
    assert not merged.get('quote_aligned_start')
    assert not merged.get('quote_aligned_end')
    assert not inconclusive_bounds_supported(merged, None)


def test_estimated_pattern_cannot_widen_precise_llm_ad():
    precise = align_ad_quote_bounds([dict(_candidate(),
        detection_stage='claude', category='sponsor')], _segments())[0]
    estimated = {
        'start': 100.0, 'end': 200.0, 'confidence': 0.9,
        'detection_stage': 'text_pattern', 'category': 'sponsor',
        'sponsor': 'Acme', 'reason': 'Acme pattern', 'pattern_id': 1,
        'span_estimated': True, 'has_estimated_pattern_member': True,
        'text_start': 110.0, 'text_end': 133.8,
    }

    merged = AdDetector.__new__(AdDetector)._merge_detection_results(
        [estimated, precise], _segments())

    assert len(merged) == 1
    assert (merged[0]['start'], merged[0]['end']) == (106.0, 187.8)
    assert merged[0]['detection_stage'] == 'claude'
    assert not merged[0].get('has_estimated_pattern_member')

    outside_text = dict(estimated, text_start=100.0, text_end=104.0)
    still_uncertain = AdDetector.__new__(AdDetector)._merge_detection_results(
        [outside_text, precise], _segments())
    assert still_uncertain[0]['start'] == 100.0
    assert still_uncertain[0]['has_estimated_pattern_member']


def test_precise_llm_ad_survives_estimated_pattern_coverage():
    precise = align_ad_quote_bounds([dict(_candidate(),
        detection_stage='claude', category='sponsor')], _segments())[0]
    detector = AdDetector(api_key='test-key')
    detector.db = None
    detector.audio_fingerprinter = None
    detector.pattern_service = None
    detector.text_pattern_matcher = MagicMock()
    detector.text_pattern_matcher.find_matches.return_value = [TextMatch(
        pattern_id=1, start=100.0, end=200.0, confidence=0.9,
        sponsor='Acme', category='sponsor', span_estimated=True,
        text_start=110.0, text_end=133.8)]
    with (patch.object(detector, 'initialize_client'),
          patch.object(detector, '_resolve_segment_action_map', return_value=None),
          patch.object(detector, 'detect_ads', return_value={
              'ads': [precise], 'status': 'success'})):
        result = detector.process_transcript(
            _segments(), podcast_name='Example Podcast',
            episode_title='Episode', slug='example-podcast',
            episode_id='episode-1', keep_content=False)

    assert result['status'] == 'success'
    assert [(ad['start'], ad['end']) for ad in result['ads']] == [(106.0, 187.8)]
    assert result['ads'][0]['detection_stage'] == 'claude'


def test_overlap_inside_quote_rejects_alignment():
    words = list(WORDS)
    words[6] = (107.1, 'from')
    assert 'quote_aligned_start' not in align_ad_quote_bounds(
        [_candidate()], _segments(words))[0]


def test_both_response_formats_preserve_quote_fields():
    response = ('[{"start": 100, "end": 200, "confidence": 0.95, '
                '"reason": "Sponsor read", "sponsor": "Acme", '
                '"start_text": "A word from our sponsor Acme", '
                '"end_text": "Thanks to Acme for supporting us"}]')
    timed = parse_ads_from_response(response)[0]
    assert timed['start_text'] == _candidate()['start_text']
    assert timed['end_text'] == _candidate()['end_text']

    response = response.replace('"start": 100, "end": 200',
                                '"start_id": 0, "end_id": 0')
    id_ads, used = parse_id_ads_from_response(response)
    assert used
    identified = resolve_segment_id_ads(id_ads, [dict(_segments()[0], sid=0)])[0]
    assert identified['start_text'] == timed['start_text']
    assert identified['end_text'] == timed['end_text']


def test_aligned_confirmed_ad_reaches_audio_renderer(monkeypatch):
    segments = _segments([(time - 100, word) for time, word in WORDS])
    segments[0]['start'] = 0.0
    segments[0]['end'] = 100.0
    candidate = _candidate(start=0.0, end=100.0, category='sponsor',
                           detection_stage='claude')
    candidate = align_ad_quote_bounds([candidate], segments)[0]
    assert (candidate['start'], candidate['end']) == (6.0, 87.8)

    db = MagicMock()
    db.get_setting.side_effect = lambda key: {
        'review_prompt': 'review', 'resurrect_prompt': 'resurrect',
        'review_max_boundary_shift': '60',
    }.get(key)
    reviewer = AdReviewer(db=db, llm_client=MagicMock())
    reviewer._llm_client.messages_create.return_value = _Response(
        '[{"start": 6.0, "end": 87.8, "confidence": 0.95, '
        '"reason": "Confirmed sponsor read"}]')
    monkeypatch.setattr(processing, '_build_reviewer',
                        lambda db, detector: reviewer)
    run = _run_pipeline([candidate], {'sponsor': 'remove'},
                        segments=segments, real_refine_reviewer=True)
    assert run['result'] is True
    cuts = run['local_ap'].process_episode.call_args.args[1]
    assert len(cuts) == 1
    assert (cuts[0]['start'], cuts[0]['end']) == (6.0, 87.8)
    show_times = [0, 1, 2, 3, 89, 90, 91, 92]
    assert all(not (cuts[0]['start'] <= time < cuts[0]['end'])
               for time in show_times)


def test_quote_end_survives_dai_core_restoration_and_eof_extension():
    ad = _candidate(start=0.0, end=94.2, detection_stage='dai_differential',
                    quote_aligned_end=True, quote_end=94.2,
                    dai_core_spans=[{'start': 0.0, 'end': 94.77}])
    result = AdValidator(100.0, _segments(), splice_veto_enabled=False).validate([ad])
    assert result.accepted == 1
    assert result.ads[0]['end'] == 94.2
    assert result.ads[0]['dai_core_spans'] == [{'start': 0.0, 'end': 94.2}]
    assert result.ads[0]['end'] < 94.42


def test_merged_anchored_end_is_not_extended_through_show_tail():
    first = _candidate(start=0.0, end=45.0, start_text='', end_text='')
    second = _candidate(start=47.0, end=87.8,
                        quote_aligned_end=True, quote_end=87.8)
    result = AdValidator(100.0, _segments(), splice_veto_enabled=False).validate(
        [first, second])
    assert len(result.ads) == 1
    assert result.ads[0]['end'] == 87.8
    assert result.ads[0]['quote_aligned_end']


@pytest.mark.parametrize('addressing_mode', ['segment_ids', 'timestamps'])
def test_jev_detection_without_quotes_reaches_renderer(monkeypatch,
                                                       addressing_mode):
    segments = _segments([(time - 100, word) for time, word in WORDS])
    segments[0]['start'] = 0.0
    segments[0]['end'] = 100.0
    fine = timed_line_segments(segments)
    for sid, segment in enumerate(fine):
        segment['sid'] = sid
    bounds = ({'start_id': 1, 'end_id': 3} if addressing_mode == 'segment_ids'
              else {'start': 6.0, 'end': 87.8})
    response = json.dumps([{
        **bounds, 'category': 'sponsor',
        'confidence': 0.95, 'reason': 'Sponsor read', 'sponsor': 'Acme',
        'end_text': fine[3]['text'],
    }])
    detector = AdDetector(api_key='test-key')
    with patch.object(detector, '_call_llm_for_window', return_value=(
            SimpleNamespace(content=response, usage={}), None)):
        detection = detector._process_single_window(
            window_idx=0, window={'start': 0.0, 'end': 100.0,
                                  'segments': fine}, total_windows=1,
            model='typesafe/jev', system_prompt='detect',
            description_section='', podcast_name='Example Podcast',
            episode_title='Episode', audio_enforcer=None,
            audio_analysis=None, llm_timeout=30, max_retries=1,
            slug='example-podcast', episode_id='episode-1',
            pass_name='pass1', window_label_prefix='Window',
            validate_timestamps=True, addressing_mode=addressing_mode)
    assert len(detection.ads) == 1
    candidate = detection.ads[0]
    assert not candidate['start_text']
    assert (candidate['start'], candidate['end']) == (6.0, 87.8)
    assert (candidate['word_timed_start'], candidate['word_timed_end']) == (6.0, 87.8)

    db = MagicMock()
    db.get_setting.side_effect = lambda key: {
        'review_prompt': 'review', 'resurrect_prompt': 'resurrect',
        'review_max_boundary_shift': '60',
    }.get(key)
    reviewer = AdReviewer(db=db, llm_client=MagicMock())
    prompt = reviewer._build_user_prompt(
        ad=candidate, segments=segments,
        episode_meta={
            'podcast_name': 'Example Podcast', 'episode_title': 'Episode',
            'podcast_description': '', 'episode_description': '',
            'slug': 'example-podcast', 'episode_id': 'episode-1',
            'podcast_id': 1,
        }, pool='accepted')
    assert '[6.0s-11.8s] A word from our sponsor Acme.' in prompt
    assert '[89.0s-92.8s] Back to the story.' in prompt
    assert '[0.0s-100.0s]' not in prompt

    reviewer._llm_client.messages_create.return_value = _Response(
        '[{"start": 6.0, "end": 87.8, "confidence": 0.95, '
        '"reason": "Confirmed sponsor read"}]')
    monkeypatch.setattr(processing, '_build_reviewer',
                        lambda db, detector: reviewer)
    run = _run_pipeline([candidate], {'sponsor': 'remove'},
                        segments=segments, real_refine_reviewer=True)
    cuts = run['local_ap'].process_episode.call_args.args[1]
    assert run['result'] is True
    assert len(cuts) == 1
    assert (cuts[0]['start'], cuts[0]['end']) == (6.0, 87.8)
    assert all(not (cuts[0]['start'] <= time < cuts[0]['end'])
               for time in (0, 1, 2, 3, 89, 90, 91, 92))


def test_plain_confirmation_cuts_approved_overlap_and_holds_estimated_remainder():
    candidate = {
        'start': 19.55, 'end': 80.0, 'category': 'sponsor',
        'confidence': 0.98, 'reason': 'Acme sponsor read',
        'sponsor': 'Acme', 'detection_stage': 'text_pattern',
        'has_estimated_pattern_member': True,
    }
    corrections = [{'start': 20.0, 'end': 60.0, 'correction_type': 'confirm'}]
    result = _run_pipeline(
        [candidate], {'sponsor': 'remove'},
        segments=[{'start': 0.0, 'end': 100.0, 'text': 'Acme sponsor read'}],
        real_refine_reviewer=True, confirmed_corrections=corrections,
        reviewer_side_effect=lambda cuts, markers: (cuts, markers))

    assert result['result'] is True
    cuts = result['local_ap'].process_episode.call_args.args[1]
    assert [(cut['start'], cut['end']) for cut in cuts] == [(20.0, 60.0)]
    saved = result['storage'].save_combined_ads.call_args.args[2]
    outside = next(ad for ad in saved if ad['start'] == 60.0)
    assert outside['end'] == 100.0
    assert outside['held_for_review'] is True
    assert outside['hold_reason'] == 'estimated_pattern_bounds'
    assert outside['_skip_pattern_learning'] is True
