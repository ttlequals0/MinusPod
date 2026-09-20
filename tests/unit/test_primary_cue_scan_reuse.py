"""The original audio is scanned for template cues once per run."""
import tempfile
import unittest
from concurrent.futures import Future
from unittest.mock import MagicMock, patch

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('primary_cue_reuse_')
import main_app.processing as processing  # noqa: E402
from audio_analysis.base import AudioAnalysisResult, AudioSegmentSignal  # noqa: E402
from audio_analysis.cue_template_matcher import AudioCueTemplateMatcher  # noqa: E402


def _cue_signal(start, template_id):
    return AudioSegmentSignal(
        signal_type='audio_cue', start=start, end=start + 0.5, confidence=0.9,
        details={'source': 'template', 'template_id': template_id})


def _analysis_with_cues(signals, complete=True):
    result = AudioAnalysisResult(signals=signals)
    result.cue_scan_complete = complete
    return result


class TestTemplateCueMarks(unittest.TestCase):
    def test_marks_match_the_scan_shape(self):
        result = _analysis_with_cues([_cue_signal(12.5, 5)])
        self.assertEqual(processing._template_cue_marks(result),
                         [{'time': 12.5, 'template_id': 5}])

    def test_spectral_cues_are_not_template_marks(self):
        spectral = AudioSegmentSignal(
            signal_type='audio_cue', start=3.0, end=3.5, confidence=0.9,
            details={'source': 'spectral'})
        self.assertEqual(
            processing._template_cue_marks(_analysis_with_cues([spectral])), [])


class TestPublishPrimaryCues(unittest.TestCase):
    def test_a_completed_scan_publishes_its_marks(self):
        future = Future()
        processing._publish_primary_cues(
            future, _analysis_with_cues([_cue_signal(1.0, 2)]))
        self.assertEqual(future.result(0), [{'time': 1.0, 'template_id': 2}])

    def test_an_incomplete_scan_publishes_nothing_usable(self):
        future = Future()
        processing._publish_primary_cues(
            future, _analysis_with_cues([_cue_signal(1.0, 2)], complete=False))
        self.assertIsNone(future.result(0))

    def test_a_published_future_is_never_overwritten(self):
        future = Future()
        future.set_result([])
        processing._publish_primary_cues(future, _analysis_with_cues([]))
        self.assertEqual(future.result(0), [])


class TestDifferentialReusesTheAnalyzerScan(unittest.TestCase):
    def _run(self, future, matcher, tmp_path):
        mock_fetch = MagicMock(return_value={
            'status': 'ok', 'regions': [], 'refetch_meta': {}, 'error': None})
        with patch('main_app.processing.resolve_differential_fetch_mode',
                   return_value='on'), \
             patch('main_app.processing._feed_cue_matcher', return_value=matcher), \
             patch('main_app.processing.fetch_and_diff', mock_fetch), \
             patch.object(processing.tempfile, 'mkdtemp', return_value=str(tmp_path)), \
             patch.object(processing.status_service, 'update_job_stage'), \
             patch.object(processing.db, 'save_episode_dai_differential'):
            processing._run_differential_fetch(
                'example-podcast', 'a1b2c3d4e5f6', 'https://example.com/e.mp3',
                '/tmp/a.mp3', 7, primary_cue_future=future)
        return mock_fetch.call_args.kwargs['primary_cues']

    def setUp(self):
        self.matcher = AudioCueTemplateMatcher(templates=[])
        self.matcher.detect = MagicMock(return_value=[])

    def test_the_worker_does_not_rescan_when_analysis_published(self):
        future = Future()
        future.set_result([{'time': 12.5, 'template_id': 5}])
        with tempfile.TemporaryDirectory() as work_dir:
            get_cues = self._run(future, self.matcher, work_dir)
            self.assertEqual(get_cues(), [{'time': 12.5, 'template_id': 5}])
        self.matcher.detect.assert_not_called()

    def test_the_worker_scans_when_analysis_produced_nothing_usable(self):
        future = Future()
        future.set_result(None)
        with tempfile.TemporaryDirectory() as work_dir:
            get_cues = self._run(future, self.matcher, work_dir)
            self.assertEqual(get_cues(), [])
        self.matcher.detect.assert_called_once_with('/tmp/a.mp3')

    def test_the_worker_scans_itself_when_the_wait_times_out(self):
        with patch.object(processing, 'PRIMARY_CUE_WAIT_SECONDS', 0.01), \
                tempfile.TemporaryDirectory() as work_dir:
            get_cues = self._run(Future(), self.matcher, work_dir)
            self.assertEqual(get_cues(), [])
        self.matcher.detect.assert_called_once_with('/tmp/a.mp3')


class TestPublishingCuesNeverRaises(unittest.TestCase):
    """It is called from a finally: a raise there would mask the original
    failure and leave the differential worker waiting out its full timeout."""

    def test_a_broken_scan_result_still_releases_the_worker(self):
        future = Future()
        broken = _analysis_with_cues([])
        with patch.object(processing, '_template_cue_marks',
                          side_effect=RuntimeError('signals exploded')):
            processing._publish_primary_cues(future, broken)

        self.assertTrue(future.done())
        self.assertIsNone(future.result(0))


if __name__ == '__main__':
    unittest.main()
