"""Tests for segment category constants and provenance (issue #565, Task 1).

Covers:
- normalize_segment_category valid/invalid passthrough
- _merge_detection_results stamps category on every stage's ads (the single
  seam every detection stage's output passes through)
- parse_ads_from_response carries the LLM's raw category field through
  extraction unvalidated (normalization happens at the merge seam)
- get_episode's marker-to-payload mapping exposes category/actionApplied
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from ad_detector import AdDetector, parse_ads_from_response
from config import (
    SEGMENT_CATEGORIES, SEGMENT_ACTIONS, DEFAULT_SEGMENT_ACTION,
    normalize_segment_category,
)


class TestNormalizeSegmentCategory:

    def test_valid_categories_pass_through(self):
        for cat in SEGMENT_CATEGORIES:
            assert normalize_segment_category(cat) == cat

    def test_none_normalizes_to_sponsor(self):
        assert normalize_segment_category(None) == 'sponsor'

    def test_unknown_string_normalizes_to_sponsor(self):
        assert normalize_segment_category('advertisement') == 'sponsor'

    def test_non_str_normalizes_to_sponsor(self):
        assert normalize_segment_category(42) == 'sponsor'
        assert normalize_segment_category(['sponsor']) == 'sponsor'
        assert normalize_segment_category({'category': 'sponsor'}) == 'sponsor'

    def test_constants_shape(self):
        assert 'sponsor' in SEGMENT_CATEGORIES
        assert DEFAULT_SEGMENT_ACTION in SEGMENT_ACTIONS
        assert DEFAULT_SEGMENT_ACTION == 'remove'


def _ad(start, end, detection_stage, confidence=0.9, category=None):
    ad = {'start': start, 'end': end, 'confidence': confidence,
          'reason': 'test', 'sponsor': None,
          'detection_stage': detection_stage}
    if category is not None:
        ad['category'] = category
    return ad


class TestMergeDetectionResultsStampsCategory:
    """The merge seam (_merge_detection_results) is the single point every
    stage's ads pass through, so it is the single normalization point."""

    def _det(self):
        return AdDetector(api_key='test-key')

    def test_fingerprint_ad_defaults_to_sponsor(self):
        det = self._det()
        out = det._merge_detection_results([_ad(10.0, 40.0, 'fingerprint')])
        assert out[0]['category'] == 'sponsor'

    def test_text_pattern_ad_defaults_to_sponsor(self):
        det = self._det()
        out = det._merge_detection_results([_ad(10.0, 40.0, 'text_pattern')])
        assert out[0]['category'] == 'sponsor'

    def test_dai_differential_ad_defaults_to_sponsor(self):
        det = self._det()
        out = det._merge_detection_results([_ad(10.0, 40.0, 'dai_differential')])
        assert out[0]['category'] == 'sponsor'

    def test_llm_ad_valid_category_survives_merge(self):
        det = self._det()
        out = det._merge_detection_results(
            [_ad(10.0, 40.0, 'claude', category='cross_promo')])
        assert out[0]['category'] == 'cross_promo'

    def test_llm_ad_unknown_category_normalizes_to_sponsor(self):
        det = self._det()
        out = det._merge_detection_results(
            [_ad(10.0, 40.0, 'claude', category='advertisement')])
        assert out[0]['category'] == 'sponsor'

    def test_mixed_stage_ads_all_carry_a_valid_category(self):
        det = self._det()
        ads = [
            _ad(10.0, 40.0, 'fingerprint'),
            _ad(200.0, 240.0, 'text_pattern'),
            _ad(400.0, 440.0, 'dai_differential'),
            _ad(600.0, 640.0, 'claude', category='self_promo'),
        ]
        out = det._merge_detection_results(ads)
        assert len(out) == 4
        for marker in out:
            assert marker['category'] in SEGMENT_CATEGORIES


class TestParseAdsFromResponseCarriesCategory:
    """parse_ads_from_response passes the raw LLM category through
    unvalidated; normalize_segment_category is applied later at the merge
    seam, not here."""

    def test_valid_category_field_survives_extraction(self):
        response = json.dumps([{
            "start": 100.0, "end": 160.0, "confidence": 0.92,
            "reason": "BetterHelp ad", "sponsor": "BetterHelp",
            "category": "cross_promo",
        }])
        ads = parse_ads_from_response(response)
        assert len(ads) == 1
        assert ads[0]['category'] == 'cross_promo'

    def test_unknown_category_passes_through_unvalidated(self):
        response = json.dumps([{
            "start": 100.0, "end": 160.0, "confidence": 0.92,
            "reason": "ad", "sponsor": "X", "category": "advertisement",
        }])
        ads = parse_ads_from_response(response)
        assert ads[0]['category'] == 'advertisement'

    def test_missing_category_field_stays_absent(self):
        response = json.dumps([{
            "start": 100.0, "end": 160.0, "confidence": 0.92,
            "reason": "ad", "sponsor": "X",
        }])
        ads = parse_ads_from_response(response)
        assert 'category' not in ads[0]


def _episode_marker_payload_fields(markers):
    """Replicates the get_episode category/actionApplied mapping (see
    src/api/episodes.py) without spinning up Flask/DB, matching the
    _split_markers idiom used by test_pending_review_bucket.py."""
    out = []
    for marker in markers:
        marker = dict(marker)
        marker['category'] = marker.get('category', 'sponsor')
        marker['actionApplied'] = marker.get('action_applied')
        out.append(marker)
    return out


class TestEpisodePayloadCategoryFields:

    def test_defaults_when_marker_has_neither_field(self):
        out = _episode_marker_payload_fields([{'start': 1.0, 'end': 2.0}])
        assert out[0]['category'] == 'sponsor'
        assert out[0]['actionApplied'] is None

    def test_existing_category_and_action_applied_preserved(self):
        out = _episode_marker_payload_fields([{
            'start': 1.0, 'end': 2.0,
            'category': 'self_promo', 'action_applied': 'beep',
        }])
        assert out[0]['category'] == 'self_promo'
        assert out[0]['actionApplied'] == 'beep'
