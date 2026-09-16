"""A retryable failure to fetch the enclosure is not a run of the episode."""
import unittest
from unittest.mock import patch

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('transient_download_run_')
import main_app.processing as processing  # noqa: E402
from main_app.processing import AudioNotReadyError  # noqa: E402


class TestDownloadRaisesTypedError(unittest.TestCase):
    def test_cdn_not_ready_raises_the_typed_error(self):
        with patch.object(processing.transcriber, 'check_audio_availability',
                          return_value=(False, 'CDN not ready (404)')):
            with self.assertRaises(AudioNotReadyError) as caught:
                processing._download_episode_audio('https://cdn.example.com/e.mp3')
        self.assertIn('CDN not ready', str(caught.exception))
        self.assertTrue(processing.is_transient_error(caught.exception))

    def test_a_failed_download_raises_the_typed_error_too(self):
        with patch.object(processing.transcriber, 'check_audio_availability',
                          return_value=(True, None)), \
             patch.object(processing.transcriber, 'download_audio', return_value=None):
            with self.assertRaises(AudioNotReadyError):
                processing._download_episode_audio('https://cdn.example.com/e.mp3')


class TestHistoryRecording(unittest.TestCase):
    def _run_failure(self, error, retry_count=0):
        episode_data = {'retry_count': retry_count}
        with patch.object(processing, 'db') as db, \
             patch.object(processing, '_record_history_row') as record, \
             patch.object(processing, '_require_publication_owner'), \
             patch.object(processing, '_publish_status'), \
             patch.object(processing, 'get_episode_token_totals',
                          return_value={'input_tokens': 0, 'output_tokens': 0,
                                        'cost': 0.0}), \
             patch.object(processing, 'is_offline_queue_enabled', return_value=False), \
             patch.object(processing, 'fire_event'):
            db.get_episode.return_value = episode_data
            processing._handle_processing_failure(
                'example-podcast', 'a1b2c3d4e5f6', 'An Episode', 'Example Show',
                episode_data, error, start_time=0.0)
            return db, record

    def test_retryable_cdn_404_records_no_run(self):
        db, record = self._run_failure(AudioNotReadyError('CDN not ready (404)'))

        record.assert_not_called()
        # The episode row still carries the error and the retry budget.
        status_kwargs = db.upsert_episode.call_args.kwargs
        self.assertEqual(status_kwargs['retry_count'], 1)
        self.assertIn('CDN not ready', status_kwargs['error_message'])

    def test_the_final_attempt_is_recorded(self):
        _db, record = self._run_failure(
            AudioNotReadyError('CDN not ready (404)'),
            retry_count=processing.MAX_EPISODE_RETRIES - 1)

        record.assert_called_once()

    def test_a_failure_after_the_download_is_still_a_run(self):
        _db, record = self._run_failure(RuntimeError('detection blew up'))

        record.assert_called_once()


if __name__ == '__main__':
    unittest.main()
