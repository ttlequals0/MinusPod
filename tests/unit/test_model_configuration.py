"""Tests for model resolvers requiring an explicit configured model (no hardcoded default)."""
from unittest.mock import MagicMock, patch

import pytest

from tests.app_bootstrap import bootstrap
bootstrap('model_config_test_')

from ad_detector import AdDetector
import chapters_generator
from config import ModelNotConfiguredError


def _detector(settings):
    detector = AdDetector(api_key='test-key')
    detector.db = MagicMock()
    detector.db.get_setting.side_effect = lambda k: settings.get(k)
    return detector


def test_get_model_returns_configured_value():
    detector = _detector({'claude_model': 'claude-sonnet-5'})
    assert detector.get_model() == 'claude-sonnet-5'


def test_get_model_raises_with_setting_key_when_unset():
    detector = _detector({})
    with pytest.raises(ModelNotConfiguredError, match='claude_model'):
        detector.get_model()


def test_get_model_returns_stale_value_unchanged():
    detector = _detector({'claude_model': 'some-retired-model-id'})
    assert detector.get_model() == 'some-retired-model-id'


def test_get_verification_model_returns_configured_value():
    detector = _detector({
        'claude_model': 'claude-sonnet-5',
        'verification_model': 'claude-opus-4-8',
    })
    assert detector.get_verification_model() == 'claude-opus-4-8'


def test_get_verification_model_falls_back_to_detection_model():
    detector = _detector({'claude_model': 'claude-sonnet-5'})
    assert detector.get_verification_model() == 'claude-sonnet-5'


def test_get_verification_model_raises_when_both_unset():
    detector = _detector({})
    with pytest.raises(ModelNotConfiguredError, match='claude_model'):
        detector.get_verification_model()


def _patched_chapters_db(settings):
    db = MagicMock()
    db.get_setting.side_effect = lambda k: settings.get(k)
    return patch.object(chapters_generator, 'Database', return_value=db)


def test_get_chapters_model_returns_configured_value():
    with _patched_chapters_db({'chapters_model': 'claude-haiku-4-5-20251001'}):
        assert chapters_generator.get_chapters_model() == 'claude-haiku-4-5-20251001'


def test_get_chapters_model_falls_back_to_detection_model():
    with _patched_chapters_db({'claude_model': 'claude-sonnet-5'}):
        assert chapters_generator.get_chapters_model() == 'claude-sonnet-5'


def test_get_chapters_model_raises_when_both_unset():
    with _patched_chapters_db({}):
        with pytest.raises(ModelNotConfiguredError, match='chapters_model'):
            chapters_generator.get_chapters_model()
