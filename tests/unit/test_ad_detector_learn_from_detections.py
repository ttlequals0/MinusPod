"""Tests for Gate B in AdDetector.learn_from_detections."""
from unittest.mock import MagicMock

import pytest

from ad_detector import AdDetector


@pytest.fixture
def detector():
    """AdDetector with mocked DB, text_pattern_matcher, sponsor_service, fingerprinter."""
    det = AdDetector(api_key="test-key")
    det._db = MagicMock()
    det._db.get_active_pattern_sponsors = MagicMock(return_value=set())
    det._text_pattern_matcher = MagicMock()
    det._text_pattern_matcher.create_pattern_from_ad = MagicMock(return_value=None)
    det._sponsor_service = MagicMock()
    det._sponsor_service.get_sponsors = MagicMock(return_value=[])
    det._sponsor_service.find_sponsor_in_text = MagicMock(return_value=False)
    det._audio_fingerprinter = None
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
        detector._text_pattern_matcher.create_pattern_from_ad.assert_not_called()

    def test_passes_when_sponsor_in_registry(self, detector):
        # Real find_sponsor_in_text returns the canonical sponsor name or None.
        # The test ad uses a name not in KNOWN_SHORT_BRANDS so only Gate B
        # via the registry should let it through.
        detector._sponsor_service.find_sponsor_in_text.return_value = "Foobr"
        detector.learn_from_detections(
            [_ad("Foobr")], _segments(), podcast_id="podA", episode_id="ep1"
        )
        detector._text_pattern_matcher.create_pattern_from_ad.assert_called_once()

    def test_passes_when_pattern_exists_for_sponsor(self, detector):
        detector._db.get_active_pattern_sponsors.return_value = {"pura"}
        detector.learn_from_detections(
            [_ad("Pura")], _segments(), podcast_id="podA", episode_id="ep1"
        )
        detector._text_pattern_matcher.create_pattern_from_ad.assert_called_once()

    def test_passes_for_known_short_brand_seed(self, detector):
        detector.learn_from_detections(
            [_ad("Xero")], _segments(), podcast_id="podA", episode_id="ep1"
        )
        detector._text_pattern_matcher.create_pattern_from_ad.assert_called_once()

    def test_long_name_bypasses_gate_b_entirely(self, detector):
        detector.learn_from_detections(
            [_ad("LongerName")], _segments(), podcast_id="podA", episode_id="ep1"
        )
        detector._text_pattern_matcher.create_pattern_from_ad.assert_called_once()

    def test_multi_word_short_name_bypasses_gate_b(self, detector):
        detector.learn_from_detections(
            [_ad("Ad Co")], _segments(), podcast_id="podA", episode_id="ep1"
        )
        detector._text_pattern_matcher.create_pattern_from_ad.assert_called_once()

    def test_zero_alias_canonicalized_to_xero(self, detector):
        detector.learn_from_detections(
            [_ad("Zero")], _segments(), podcast_id="podA", episode_id="ep1"
        )
        call = detector._text_pattern_matcher.create_pattern_from_ad.call_args
        assert call is not None
        assert call.kwargs["sponsor"] == "Xero"
