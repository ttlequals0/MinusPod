"""Tests for tunable learning confidence thresholds (Task 4).

record_verification_misses (pattern_service) and learn_from_detections /
_ad_passes_learning_filters (ad_detector) must read learning_min_confidence
and learning_min_confidence_long from the DB at call time instead of the
LEARNING_MIN_CONFIDENCE / _LONG constants directly, so a settings change
takes effect on the next run without any caching.
"""
import os
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from database import Database  # noqa: E402
from pattern_service import PatternService  # noqa: E402
from ad_detector import AdDetector  # noqa: E402


@pytest.fixture
def db(tmp_path):
    Database._instance = None  # type: ignore[attr-defined]
    if hasattr(Database, '_initialized'):
        Database._initialized = False  # type: ignore[attr-defined]
    inst = Database(data_dir=str(tmp_path))
    yield inst
    Database._instance = None  # type: ignore[attr-defined]


@pytest.fixture
def detector():
    """AdDetector with a mocked DB (get_setting_float configurable per test)."""
    det = AdDetector(api_key="test-key")
    det.db = MagicMock()
    det.db.get_active_pattern_sponsors = MagicMock(return_value=set())
    det.text_pattern_matcher = MagicMock()
    det.text_pattern_matcher.create_pattern_from_ad = MagicMock(return_value=None)
    det.sponsor_service = MagicMock()
    det.sponsor_service.get_sponsors = MagicMock(return_value=[])
    det.sponsor_service.find_sponsor_in_text = MagicMock(return_value="Xero")
    det.audio_fingerprinter = None
    return det


def _real_ad_window():
    return (
        "BetterHelp therapy can help you live a more empowered life. "
        "Visit them online to start with a licensed therapist today. "
        "BetterHelp matches you in 24 hours."
    )


def _segments(text, start=0.0, end=60.0):
    return [{'start': start, 'end': end, 'text': text}]


def _claude_ad(confidence, duration):
    return {
        'was_cut': True,
        'detection_stage': 'claude',
        'confidence': confidence,
        'start': 0.0,
        'end': duration,
    }


class TestPatternServiceConfigurableThreshold:
    """record_verification_misses (~pattern_service.py:833-845)."""

    def test_configured_low_threshold_admits_0_6_confidence_miss(self, db):
        db.set_setting('learning_min_confidence', '0.5')
        db.set_setting('learning_min_confidence_long', '0.5')
        svc = PatternService(db=db)
        svc.record_verification_misses(
            'some-show', 'abc',
            [{
                'sponsor': 'BetterHelp', 'start': 0.0, 'end': 60.0,
                'confidence': 0.6,
                'reason': 'BetterHelp host-read',
            }],
            segments=_segments(_real_ad_window()),
        )
        assert len(db.get_ad_patterns(active_only=True)) == 1

    def test_default_threshold_still_rejects_0_6_confidence_miss(self, db):
        svc = PatternService(db=db)
        svc.record_verification_misses(
            'some-show', 'abc',
            [{
                'sponsor': 'BetterHelp', 'start': 0.0, 'end': 60.0,
                'confidence': 0.6,
                'reason': 'BetterHelp host-read',
            }],
            segments=_segments(_real_ad_window()),
        )
        assert db.get_ad_patterns(active_only=True) == []


class TestAdPassesLearningFiltersConfigurableLongThreshold:
    """_ad_passes_learning_filters long-duration check (~ad_detector/__init__.py:1576-1610)."""

    def test_configured_low_long_threshold_admits_0_6_confidence_100s_ad(self, detector):
        detector.db.get_setting_float = MagicMock(side_effect=lambda key, default: 0.5)
        assert detector._ad_passes_learning_filters(
            _claude_ad(0.6, 100.0), min_confidence=0.5
        ) is True

    def test_default_long_threshold_still_rejects_0_6_confidence_100s_ad(self, detector):
        detector.db.get_setting_float = MagicMock(side_effect=lambda key, default: default)
        assert detector._ad_passes_learning_filters(
            _claude_ad(0.6, 100.0), min_confidence=0.6
        ) is False


class TestLearnFromDetectionsConfigurableThreshold:
    """learn_from_detections min_confidence read (~ad_detector/__init__.py:1747)."""

    def test_configured_low_threshold_lets_0_6_confidence_ad_through(self, detector):
        detector.db.get_setting_float = MagicMock(side_effect=lambda key, default: 0.5)
        segments = [{
            'start': 0, 'end': 60,
            'text': 'Xero is the accounting platform for small business.',
        }]
        ad = _claude_ad(0.6, 60.0)
        ad['sponsor'] = 'Xero'
        detector.learn_from_detections([ad], segments, podcast_id='podA', episode_id='ep1')
        detector.text_pattern_matcher.create_pattern_from_ad.assert_called_once()

    def test_default_threshold_still_rejects_0_6_confidence_ad(self, detector):
        detector.db.get_setting_float = MagicMock(side_effect=lambda key, default: default)
        segments = [{
            'start': 0, 'end': 60,
            'text': 'Xero is the accounting platform for small business.',
        }]
        ad = _claude_ad(0.6, 60.0)
        ad['sponsor'] = 'Xero'
        detector.learn_from_detections([ad], segments, podcast_id='podA', episode_id='ep1')
        detector.text_pattern_matcher.create_pattern_from_ad.assert_not_called()
