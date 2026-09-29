"""Tests for Gate B in AdDetector.learn_from_detections."""
from unittest.mock import MagicMock

import pytest

from ad_detector import AdDetector
from ad_validator import AdValidator
from utils.markers import mark_distinct_merge


@pytest.fixture
def detector():
    """AdDetector with mocked DB, text_pattern_matcher, sponsor_service, fingerprinter."""
    det = AdDetector(api_key="test-key")
    det.db = MagicMock()
    det.db.get_active_pattern_sponsors = MagicMock(return_value=set())
    det.db.get_setting_float = MagicMock(side_effect=lambda key, default: default)
    det.text_pattern_matcher = MagicMock()
    det.text_pattern_matcher.create_patterns_from_ad = MagicMock(return_value=[])
    det.sponsor_service = MagicMock()
    det.sponsor_service.get_sponsors = MagicMock(return_value=[])
    det.sponsor_service.find_sponsor_in_text = MagicMock(return_value=False)
    det.audio_fingerprinter = None
    return det


def _segments():
    return [
        {"start": 0, "end": 60, "text": "Xero is the accounting platform for small business."},
    ]


def _ad(sponsor, start=0.0, end=60.0):
    return {
        "sponsor": sponsor,
        "start": start,
        "end": end,
        "was_cut": True,
        "detection_stage": "claude",
        "confidence": 0.95,
    }


class TestGateBShortSponsor:

    def test_rejects_unknown_short_single_word(self, detector):
        detector.learn_from_detections(
            [_ad("Foobr")], _segments(), podcast_id="podA", episode_id="ep1"
        )
        detector.text_pattern_matcher.create_patterns_from_ad.assert_not_called()

    def test_passes_when_sponsor_in_registry(self, detector):
        # Real find_sponsor_in_text returns the canonical sponsor name or None.
        # The test ad uses a name not in KNOWN_SHORT_BRANDS so only Gate B
        # via the registry should let it through.
        detector.sponsor_service.find_sponsor_in_text.return_value = "Foobr"
        detector.learn_from_detections(
            [_ad("Foobr")], _segments(), podcast_id="podA", episode_id="ep1"
        )
        detector.text_pattern_matcher.create_patterns_from_ad.assert_called_once()

    def test_passes_when_pattern_exists_for_sponsor(self, detector):
        detector.db.get_active_pattern_sponsors.return_value = {"pura"}
        detector.learn_from_detections(
            [_ad("Pura")], _segments(), podcast_id="podA", episode_id="ep1"
        )
        detector.text_pattern_matcher.create_patterns_from_ad.assert_called_once()

    def test_passes_for_known_short_brand_seed(self, detector):
        detector.learn_from_detections(
            [_ad("Xero")], _segments(), podcast_id="podA", episode_id="ep1"
        )
        detector.text_pattern_matcher.create_patterns_from_ad.assert_called_once()

    def test_long_name_bypasses_gate_b_entirely(self, detector):
        detector.learn_from_detections(
            [_ad("LongerName")], _segments(), podcast_id="podA", episode_id="ep1"
        )
        detector.text_pattern_matcher.create_patterns_from_ad.assert_called_once()

    def test_multi_word_short_name_bypasses_gate_b(self, detector):
        detector.learn_from_detections(
            [_ad("Ad Co")], _segments(), podcast_id="podA", episode_id="ep1"
        )
        detector.text_pattern_matcher.create_patterns_from_ad.assert_called_once()

    def test_zero_alias_canonicalized_to_xero(self, detector):
        detector.learn_from_detections(
            [_ad("Zero")], _segments(), podcast_id="podA", episode_id="ep1"
        )
        call = detector.text_pattern_matcher.create_patterns_from_ad.call_args
        assert call is not None
        assert call.kwargs["sponsor"] == "Xero"


def test_learning_skips_silent_remainder_cut_with_the_ad(detector):
    read = {"start": 100.0, "end": 160.0, "text": "brought to you by LongerName use promo code show"}
    silence = {"silence_spans": [{"start": 160.0, "end": 200.0, "duration": 40.0}]}
    ad = {"start": 100.0, "end": 160.0, "confidence": 0.95, "sponsor": "LongerName",
          "reason": "LongerName sponsor read", "detection_stage": "claude"}
    mark_distinct_merge(ad, {"start": 150.0, "end": 200.0, "confidence": 0.95,
                             "detection_stage": "text_pattern", "span_estimated": True,
                             "text_start": 150.0, "text_end": 160.0,
                             "has_estimated_pattern_member": True})
    ad["end"] = 200.0

    cut = AdValidator(3600.0, [read], splice_veto_enabled=False).validate(
        [ad], audio_analysis=silence).ads
    assert [(a["start"], a["end"]) for a in cut] == [(100.0, 200.0)]
    cut[0]["was_cut"] = True

    detector.learn_from_detections(cut, [read], podcast_id="podA", episode_id="ep1")

    call = detector.text_pattern_matcher.create_patterns_from_ad.call_args
    assert (call.kwargs["start"], call.kwargs["end"]) == (100.0, 160.0)
