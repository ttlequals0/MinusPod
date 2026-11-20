"""A retry scheduled for a recognized upstream failure logs at WARNING; everything else stays at ERROR."""
import logging
import unittest
from unittest.mock import patch

import requests

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('failure_log_level_')
import main_app.processing as processing  # noqa: E402
from config import ModelNotConfiguredError
from main_app.pipeline import common, finalize, failure
from main_app.pipeline.transcribe import AudioNotReadyError


class TestFailureLogLevel(unittest.TestCase):
    def _failed_levels(self, error, retry_count=0):
        episode_data = {'retry_count': retry_count}
        totals = {'input_tokens': 0, 'output_tokens': 0, 'cost': 0.0}
        with patch.object(processing, 'db') as db, \
             patch.object(finalize, '_record_history_row'), \
             patch.object(common, '_require_publication_owner'), \
             patch.object(common, '_publish_status'), \
             patch.object(failure, 'get_episode_token_totals', return_value=totals), \
             patch.object(failure, 'is_offline_queue_enabled', return_value=False), \
             patch.object(failure, 'fire_event'), \
             self.assertLogs('podcast.audio', level='DEBUG') as logs:
            db.get_episode.return_value = episode_data
            failure._handle_processing_failure(
                'example-podcast', 'a1b2c3d4e5f6', 'An Episode', 'Example Show',
                episode_data, error, start_time=0.0)
        return [r.levelno for r in logs.records if ' Failed: ' in r.getMessage()]

    def test_retryable_not_ready_audio_warns(self):
        levels = self._failed_levels(AudioNotReadyError('CDN not ready (404)'))
        self.assertEqual(levels, [logging.WARNING])

    def test_not_ready_audio_on_the_last_attempt_is_an_error(self):
        levels = self._failed_levels(
            AudioNotReadyError('CDN not ready (404)'),
            retry_count=failure.MAX_EPISODE_RETRIES - 1)
        self.assertEqual(levels, [logging.ERROR])

    def test_scheduled_transient_retry_warns(self):
        error = requests.HTTPError('404 Client Error: Not Found for url: https://cdn.example.com/media.mp3')
        self.assertEqual(self._failed_levels(error), [logging.WARNING])

    def test_transient_failure_on_the_last_attempt_is_an_error(self):
        error = requests.HTTPError('404 Client Error: Not Found for url: https://cdn.example.com/media.mp3')
        levels = self._failed_levels(error, retry_count=failure.MAX_EPISODE_RETRIES - 1)
        self.assertEqual(levels, [logging.ERROR])

    def test_a_real_failure_is_an_error(self):
        levels = self._failed_levels(RuntimeError('unexpected state'))
        self.assertEqual(levels, [logging.ERROR])

    def test_a_permanent_failure_is_an_error(self):
        levels = self._failed_levels(ModelNotConfiguredError('no model'))
        self.assertEqual(levels, [logging.ERROR])

    def test_an_auth_outage_is_an_error(self):
        error = requests.HTTPError('502 Bad Gateway')
        error.auth_cause = True
        self.assertEqual(self._failed_levels(error), [logging.ERROR])


if __name__ == '__main__':
    unittest.main()
