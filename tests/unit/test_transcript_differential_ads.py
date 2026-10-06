"""Stage 2.6 markers from upstream transcript gaps and their merge-time hold release."""
import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from ad_detector import AdDetector, transcript_differential_ads
from ad_validator import AdValidator
from config import HOLD_REASON_TRANSCRIPT_DIFFERENTIAL, is_pending_review
from utils.markers import TRANSCRIPT_SPAN

SPAN = {'start': 100.0, 'end': 160.0, 'words': 150, 'offset_confirmed': False,
        'text_preview': 'this episode is brought to you by'}


def _held_marker(**overrides):
    return transcript_differential_ads([dict(SPAN, **overrides)])[0]


def _claude(start, end, confidence=0.9):
    return {'start': start, 'end': end, 'confidence': confidence,
            'reason': 'Sponsor read for Acme', 'sponsor': 'Acme',
            'detection_stage': 'claude', 'category': 'sponsor'}


def _merge(ads):
    return AdDetector(api_key='test-key')._merge_detection_results(ads)


def test_uncorroborated_span_is_held_with_the_stage_keys():
    ad = _held_marker()
    assert (ad['start'], ad['end']) == (100.0, 160.0)
    assert ad['confidence'] == 0.6
    assert ad['sponsor'] is None
    assert ad['detection_stage'] == 'transcript_differential'
    assert ad['category'] == 'sponsor'
    assert ad['reason'] == 'Upstream transcript omits this span'
    assert ad['held_for_review'] is True
    assert ad['was_cut'] is False
    assert ad['hold_reason'] == HOLD_REASON_TRANSCRIPT_DIFFERENTIAL
    assert ad['transcript_differential_uncorroborated'] is True
    assert ad[TRANSCRIPT_SPAN] == {'start': 100.0, 'end': 160.0, 'words': 150,
                                   'offset_confirmed': False}
    assert is_pending_review(ad) is True


def test_offset_confirmed_raises_confidence_only():
    ad = _held_marker(offset_confirmed=True)
    assert ad['confidence'] == 0.75
    assert ad['held_for_review'] is True
    assert ad[TRANSCRIPT_SPAN]['offset_confirmed'] is True


def test_false_positive_region_excluded_and_empty_inputs():
    assert transcript_differential_ads([SPAN], [(95.0, 165.0)]) == []
    assert transcript_differential_ads(None) == []
    assert transcript_differential_ads([]) == []


def test_merge_releases_hold_on_llm_overlap():
    merged = _merge([_held_marker(), _claude(98.0, 150.0)])
    assert len(merged) == 1
    ad = merged[0]
    assert ad.get('held_for_review') is not True
    assert 'hold_reason' not in ad
    assert 'transcript_differential_uncorroborated' not in ad
    assert ad['detection_stage'] == 'claude'
    assert ad[TRANSCRIPT_SPAN]['start'] == 100.0
    assert ad['category'] == 'sponsor'
    assert (ad['start'], ad['end']) == (98.0, 160.0)
    assert ad['confidence'] == 0.9


def test_merge_releases_hold_when_transcript_marker_sorts_first():
    fp = {'start': 105.0, 'end': 170.0, 'confidence': 0.95, 'reason': 'Fingerprint match',
          'detection_stage': 'fingerprint'}
    merged = _merge([fp, _held_marker()])
    assert len(merged) == 1
    assert merged[0].get('held_for_review') is not True
    assert merged[0]['detection_stage'] == 'fingerprint'
    assert merged[0][TRANSCRIPT_SPAN]['end'] == 160.0


def test_merge_keeps_hold_below_half_of_the_span():
    claude = _claude(140.0, 200.0)
    merged = _merge([_held_marker(), claude])
    assert len(merged) == 2
    held = next(m for m in merged if m['detection_stage'] == 'transcript_differential')
    llm = next(m for m in merged if m['detection_stage'] == 'claude')
    assert held['held_for_review'] is True
    assert held['hold_reason'] == HOLD_REASON_TRANSCRIPT_DIFFERENTIAL
    assert (llm['start'], llm['end']) == (140.0, 200.0)
    assert llm.get('held_for_review') is not True


def test_adjacent_marker_does_not_release():
    merged = _merge([_held_marker(), _claude(161.0, 200.0)])
    assert len(merged) == 2
    assert merged[0]['held_for_review'] is True


def test_held_cross_fetch_marker_is_not_a_releasing_stage():
    dai = {'start': 100.0, 'end': 160.0, 'confidence': 0.95, 'reason': 'Audio differs',
           'detection_stage': 'dai_differential', 'held_for_review': True, 'was_cut': False,
           'hold_reason': 'differential_uncorroborated', 'differential_uncorroborated': True}
    merged = _merge([dai, _held_marker()])
    assert all(m['held_for_review'] for m in merged)
    assert any(m.get('transcript_differential_uncorroborated') for m in merged)


def test_process_transcript_runs_stage_after_cross_fetch():
    detector = AdDetector(api_key='test-key')
    with patch.object(detector, 'initialize_client'), \
         patch.object(detector, 'detect_ads',
                      return_value={'ads': [], 'status': 'success',
                                    'raw_response': '', 'model': 'm'}):
        result = detector.process_transcript(
            [{'start': 0.0, 'end': 300.0, 'text': 'hello'}],
            slug='s', episode_id='e1', skip_patterns=True,
            transcript_spans=[SPAN], keep_content=False)
    assert result['detection_stats']['transcript_differential_matches'] == 1
    ads = result['ads']
    assert len(ads) == 1
    assert ads[0]['detection_stage'] == 'transcript_differential'
    assert ads[0]['held_for_review'] is True


def _pattern(start, end, **fields):
    base = dict(start=start, end=end, confidence=0.95, sponsor=None, pattern_id=1,
                category=None, defined=False, match_type='exact', span_estimated=False,
                text_start=None, text_end=None, absorbed_ids=[])
    return SimpleNamespace(**(base | fields))


def _detect(patterns, llm_ads, action_map=None):
    detector = AdDetector(api_key='test-key')
    detector.text_pattern_matcher = MagicMock()
    detector.text_pattern_matcher.find_matches.return_value = patterns
    with patch.object(detector, 'initialize_client'), \
         patch.object(detector, 'detect_ads',
                      return_value={'ads': llm_ads, 'status': 'success',
                                    'raw_response': '', 'model': 'm'}):
        return detector.process_transcript(
            [{'start': 0.0, 'end': 300.0, 'text': 'hello'}], slug='s', episode_id='e1',
            transcript_spans=[SPAN], keep_content=False, action_map=action_map)['ads']


def test_keep_action_split_leaves_the_gap_remainder_held():
    ads = _detect([_pattern(95.0, 150.0, category='intro')], [],
                  action_map={'intro': 'keep', 'sponsor': 'remove'})
    gaps = [m for m in ads if m['detection_stage'] == 'transcript_differential']
    assert gaps and all(m['held_for_review'] for m in gaps)
    validated = AdValidator(600.0, [], episode_description='',
                            transcript_spans=[SPAN]).validate(gaps).ads
    assert all(a.get('hold_reason') == HOLD_REASON_TRANSCRIPT_DIFFERENTIAL for a in validated)


def test_dropped_estimated_pattern_does_not_widen_a_precise_llm_ad():
    estimated = _pattern(98.0, 165.0, span_estimated=True, text_start=151.0, text_end=157.0)
    claude = dict(_claude(150.0, 158.0, confidence=0.95),
                  word_timed_start=150.0, word_timed_end=158.0)
    ads = _detect([estimated], [claude])
    llm = next(m for m in ads if m['detection_stage'] == 'claude')
    assert (llm['start'], llm['end']) == (150.0, 158.0)
    assert any(m['detection_stage'] == 'transcript_differential' and m['held_for_review']
               for m in ads)
