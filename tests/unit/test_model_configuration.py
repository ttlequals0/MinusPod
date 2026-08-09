"""Tests for model resolvers requiring an explicit configured model (no hardcoded default)."""
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import pytest

from tests.app_bootstrap import bootstrap
bootstrap('model_config_test_')

from ad_detector import AdDetector
import chapters_generator
from config import MAX_EPISODE_RETRIES, ModelNotConfiguredError
from llm_client import is_retryable_error
from main_app import db, processing
from main_app.episode_context import EpisodeContext
from main_app.processing import _handle_processing_failure, is_transient_error


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


class TestPermanentClassification:
    """ModelNotConfiguredError must never be treated as transient/retryable."""

    def test_is_transient_error_is_false(self):
        assert is_transient_error(ModelNotConfiguredError('claude_model')) is False

    def test_is_retryable_error_is_false(self):
        assert is_retryable_error(ModelNotConfiguredError('claude_model')) is False


MODEL_RETRY_SLUG = 'model-not-configured-retry-budget-feed'


@pytest.fixture
def seeded_model_episode():
    db.create_podcast(MODEL_RETRY_SLUG, 'https://example.com/feed.xml',
                      title='Model Retry Budget Test')
    db.upsert_episode(MODEL_RETRY_SLUG, 'ep-1', title='Episode 1', status='processing',
                      original_url='https://example.com/ep1.mp3',
                      retry_count=MAX_EPISODE_RETRIES - 1)
    yield 'ep-1'
    db.delete_podcast(MODEL_RETRY_SLUG)


class TestModelNotConfiguredRetryBudget:
    """Mirrors tests/unit/test_auth_error_retry_budget.py: a permanent error must
    not consume the retry ladder, it must fail the episode outright."""

    def test_does_not_burn_retry_count_and_fails_permanently(self, seeded_model_episode):
        episode_data = db.get_episode(MODEL_RETRY_SLUG, seeded_model_episode)
        error = ModelNotConfiguredError('claude_model')
        with patch('main_app.processing.status_service'):
            _handle_processing_failure(MODEL_RETRY_SLUG, seeded_model_episode, 'Episode 1',
                                       'Model Retry Budget Test', episode_data, error,
                                       start_time=0.0)
        episode = db.get_episode(MODEL_RETRY_SLUG, seeded_model_episode)
        assert episode['retry_count'] == MAX_EPISODE_RETRIES - 1  # unchanged
        assert episode['status'] == 'permanently_failed'
        assert episode['error_message'] == str(error)


class TestDetectionFailureSurface:
    """When get_model() raises inside detect_ads(), the episode-facing error
    must be the exact exception text, marked non-retryable."""

    def test_detect_ads_returns_clean_message_and_non_retryable(self):
        detector = _detector({})  # claude_model unset
        error = ModelNotConfiguredError('claude_model')
        with patch.object(detector, 'initialize_client'):
            result = detector.detect_ads(
                [{'start': 0.0, 'end': 10.0, 'text': 'hello world'}],
                podcast_name='Test', episode_title='Ep', slug='test', episode_id='e1')
        assert result['status'] == 'failed'
        assert result['retryable'] is False
        assert result['error'] == str(error)


class TestChaptersDegradeOnUnconfiguredModel:
    """Chapters must degrade (fallback titles/boundaries), never fail the episode."""

    def test_topic_boundaries_returns_none_and_records_message(self):
        generator = chapters_generator.ChaptersGenerator(api_key='test-key')
        generator._llm_client = MagicMock()
        error = ModelNotConfiguredError('chapters_model')
        with patch.object(chapters_generator, 'get_chapters_model', side_effect=error):
            result = generator._detect_topic_boundaries('transcript text', 0.0, 100.0, 2)
        assert result is None
        assert generator._model_not_configured_message == str(error)

    def test_chapter_titles_degrade_to_generic_and_record_message(self):
        generator = chapters_generator.ChaptersGenerator(api_key='test-key')
        generator._llm_client = MagicMock()
        chapters = [{'startTime': 0, 'title': None, 'source': 'auto', 'needs_title': True}]
        segments = [{'start': 0, 'end': 10, 'text': 'hello world'}]
        error = ModelNotConfiguredError('chapters_model')
        with patch.object(chapters_generator, 'get_chapters_model', side_effect=error):
            result = generator.generate_chapter_titles(chapters, segments, 'Pod', 'Ep')
        assert generator._title_generation_failed is True
        assert generator._model_not_configured_message == str(error)
        assert result[0]['title'] == 'Introduction'  # generic fallback, not a crash

    def test_generate_chapters_degrades_with_actionable_reason(self):
        generator = chapters_generator.ChaptersGenerator(api_key='test-key')
        generator._llm_client = MagicMock()
        segments = [{'start': 0.0, 'end': 60.0, 'text': 'short episode transcript'}]
        error = ModelNotConfiguredError('chapters_model')
        with patch.object(chapters_generator, 'get_chapters_model', side_effect=error):
            result = generator.generate_chapters(segments, episode_id='ep1')
        assert result['chapters']  # episode still produces chapters, not a failure
        assert generator.chapters_degraded is True
        assert str(error) in generator.chapters_degradation_reason


class TestBootLogsMissingModelSettings:
    """main_app._log_missing_model_settings names exactly which of the three
    model settings are unset in one ERROR line."""

    def test_logs_error_naming_missing_settings(self, caplog):
        import logging
        from main_app import _log_missing_model_settings

        stub_db = MagicMock()
        stub_db.get_setting.side_effect = lambda k: (
            'claude-sonnet-5' if k == 'claude_model' else None)
        with caplog.at_level(logging.ERROR, logger='podcast.app'):
            _log_missing_model_settings(stub_db)
        errors = [r.message for r in caplog.records if r.levelno == logging.ERROR]
        assert len(errors) == 1
        assert 'verification_model' in errors[0]
        assert 'chapters_model' in errors[0]
        assert 'claude_model' not in errors[0]

    def test_no_log_when_all_configured(self, caplog):
        import logging
        from main_app import _log_missing_model_settings

        stub_db = MagicMock()
        stub_db.get_setting.return_value = 'claude-sonnet-5'
        with caplog.at_level(logging.ERROR, logger='podcast.app'):
            _log_missing_model_settings(stub_db)
        assert not [r for r in caplog.records if r.levelno == logging.ERROR]


class TestDetectAdsFirstPassPreservesModelNotConfiguredType:
    """Regression: _detect_ads_first_pass must not downgrade a
    model-not-configured failure to a bare Exception, which is_transient_error
    treats as transient and retries to exhaustion instead of failing fast."""

    SEGMENTS = [{'start': 0.0, 'end': 5.0, 'text': 'hello'}]

    def _run_first_pass(self, ad_result):
        """Drive the real _detect_ads_first_pass with only ad_detector/db/
        storage/status_service stubbed."""
        ctx = EpisodeContext(slug='model-not-configured-seam-feed',
                             episode_id='ep-1', podcast_id='1')
        with ExitStack() as stack:
            p = lambda *a, **k: stack.enter_context(patch.object(*a, **k))
            ad_detector_mock = p(processing, 'ad_detector')
            p(processing, 'db')
            p(processing, 'storage')
            p(processing, 'status_service')
            ad_detector_mock.process_transcript.return_value = ad_result
            processing._detect_ads_first_pass(
                ctx, self.SEGMENTS, '/tmp/ep.mp3', skip_patterns=False,
                audio_analysis_result=None, progress_callback=None,
            )

    def test_reproduction_through_the_real_seam(self, seeded_model_episode):
        resolver_error = ModelNotConfiguredError('claude_model')
        ad_result = {'status': 'failed', 'error': str(resolver_error), 'ads': [],
                     'model_not_configured': True, 'retryable': False,
                     'detection_stats': {}}

        with pytest.raises(ModelNotConfiguredError) as exc_info:
            self._run_first_pass(ad_result)

        # (a) the raised type survives the dict-to-exception conversion.
        assert exc_info.type is ModelNotConfiguredError
        assert str(exc_info.value) == str(resolver_error)
        # (b) that type classifies as permanent, not "unknown -> transient".
        assert is_transient_error(exc_info.value) is False

        # (c) the real failure handler must not burn the retry ladder.
        episode_data = db.get_episode(MODEL_RETRY_SLUG, seeded_model_episode)
        with patch('main_app.processing.status_service'):
            _handle_processing_failure(MODEL_RETRY_SLUG, seeded_model_episode, 'Episode 1',
                                       'Model Retry Budget Test', episode_data,
                                       exc_info.value, start_time=0.0)
        episode = db.get_episode(MODEL_RETRY_SLUG, seeded_model_episode)
        assert episode['retry_count'] == MAX_EPISODE_RETRIES - 1  # unchanged
        assert episode['status'] == 'permanently_failed'
