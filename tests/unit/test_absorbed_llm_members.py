"""An LLM ad absorbed by a covering pattern marker is recorded as its member."""
from unittest.mock import patch

import pytest

from tests.app_bootstrap import bootstrap

bootstrap('absorbed_llm_members_test_')

from ad_detector import AdDetector
from ad_detector.boundaries import record_absorbed_detection
from ad_validator import AdValidator, Decision
from config import HOLD_REASON_ESTIMATED_PATTERN
from text_pattern_matcher import TextMatch
from tests.unit.marker_test_utils import _ad
from utils.markers import measured_member_spans

SEGMENTS = [
    {'start': 2440.0, 'end': 2462.8, 'text': 'and now a word about Acme'},
    {'start': 2462.8, 'end': 2485.2, 'text': 'the host keeps talking about the story'},
    {'start': 2485.2, 'end': 2545.0, 'text': 'Acme makes widgets, visit acme.com'},
    {'start': 2545.0, 'end': 2600.0, 'text': 'back to the conversation'},
]


def _detect(claude_confidence, claude_end=2545.0):
    detector = AdDetector(api_key='test-key')
    detector.db = None
    detector.audio_fingerprinter = None
    detector.pattern_service = None
    detector.text_pattern_matcher = type('Matcher', (), {
        'find_matches': lambda self, *a, **k: [TextMatch(
            pattern_id=7, start=2457.8, end=2545.3, confidence=0.95,
            sponsor='Acme', category='sponsor', span_estimated=True,
            text_start=2457.8, text_end=2462.8)]})()
    claude_ad = {'start': 2485.2, 'end': claude_end,
                 'confidence': claude_confidence, 'sponsor': 'Acme',
                 'category': 'sponsor', 'reason': 'Acme sponsor read'}
    with (patch.object(detector, 'initialize_client'),
          patch.object(detector, '_resolve_segment_action_map', return_value=None),
          patch.object(detector, 'detect_ads', return_value={
              'ads': [claude_ad], 'status': 'success'})):
        result = detector.process_transcript(
            SEGMENTS, podcast_name='Example Podcast', episode_title='Episode',
            slug='example-podcast', episode_id='a1b2c3d4e5f6',
            keep_content=False)
    assert len(result['ads']) == 1
    return result['ads'][0]


def test_trimmed_llm_ad_records_covered_part_as_member():
    marker = _detect(0.98, claude_end=2562.0)

    claude = [m for m in marker['merged_member_spans'] if m['stage'] == 'claude']
    assert [(m['start'], m['end']) for m in claude] == [(2485.2, 2562.0)]

    result = AdValidator(3600.0, SEGMENTS, splice_veto_enabled=False).validate([marker])

    assert [(ad['start'], ad['end']) for ad in result.ads] == [
        (2457.8, 2485.2), (2485.2, 2562.0)]
    lead, cut = result.ads
    assert lead['hold_reason'] == HOLD_REASON_ESTIMATED_PATTERN
    assert cut['validation']['decision'] == Decision.ACCEPT.value
    assert not cut.get('held_for_review')


def test_absorbed_llm_ad_recorded_as_member():
    marker = _detect(0.98)

    assert marker['detection_stage'] == 'text_pattern'
    assert (marker['start'], marker['end']) == (2457.8, 2545.3)
    claude = [m for m in marker['merged_member_spans'] if m['stage'] == 'claude']
    assert len(claude) == 1
    assert (claude[0]['start'], claude[0]['end']) == (2485.2, 2545.0)
    assert claude[0]['confidence'] == 0.98
    assert claude[0]['precise_start'] is False
    assert claude[0]['precise_end'] is False
    assert (2485.2, 2545.0, True) in measured_member_spans(marker, 0.8)
    assert not marker.get('merged_distinct_ads')


def test_absorbed_llm_ad_splits_estimated_marker():
    validator = AdValidator(3600.0, SEGMENTS, splice_veto_enabled=False)

    result = validator.validate([_detect(0.98)])

    spans = [(ad['start'], ad['end']) for ad in result.ads]
    assert spans == [(2457.8, 2485.2), (2485.2, 2545.0)]
    lead, cut = result.ads
    assert lead['held_for_review'] is True
    assert lead['hold_reason'] == HOLD_REASON_ESTIMATED_PATTERN
    assert cut['validation']['decision'] == Decision.ACCEPT.value
    assert not cut.get('held_for_review')


@pytest.mark.parametrize('segments', [SEGMENTS, []])
def test_low_confidence_absorbed_llm_ad_is_not_an_anchor(segments):
    marker = _detect(0.6)
    assert all(not anchor or (lo, hi) != (2485.2, 2545.0)
               for lo, hi, anchor in measured_member_spans(marker, 0.8))

    result = AdValidator(3600.0, segments, splice_veto_enabled=False).validate([marker])

    assert [(ad['start'], ad['end']) for ad in result.ads] == [(2457.8, 2545.3)]
    assert result.ads[0]['hold_reason'] == HOLD_REASON_ESTIMATED_PATTERN


def test_ad_straddling_two_markers_clipped_to_each():
    markers = [_ad(100.0, 150.0, 'fingerprint', pattern_id=1),
               _ad(150.0, 200.0, 'text_pattern', pattern_id=2)]
    regions = [{'start': m['start'], 'end': m['end'],
                'pattern_id': m['pattern_id']} for m in markers]
    ad = _ad(120.0, 180.0, confidence=0.9,
             quote_aligned_start=True, quote_start=120.0,
             quote_aligned_end=True, quote_end=180.0)

    record_absorbed_detection(ad, regions, markers)

    first, second = ([s for s in m['merged_member_spans'] if s['stage'] == 'claude']
                     for m in markers)
    assert [(s['start'], s['end']) for s in first + second] == [
        (120.0, 150.0), (150.0, 180.0)]
    assert (first[0]['precise_start'], first[0]['precise_end']) == (True, False)
    assert (second[0]['precise_start'], second[0]['precise_end']) == (False, True)
    assert markers[0]['merged_protected_end'] == 150.0
    assert markers[1]['merged_protected_start'] == 150.0
