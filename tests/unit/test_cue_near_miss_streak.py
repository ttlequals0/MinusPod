"""A template stuck just under its threshold gets told what would work."""
import logging
import types
import unittest
from unittest.mock import patch

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('cue_near_miss_streak_')
import main_app.processing as processing  # noqa: E402
from audio_analysis.base import AudioAnalysisResult  # noqa: E402
from audio_analysis.cue_threshold_suggest import (  # noqa: E402
    near_miss_streak_suggestion,
)
from config import AUDIO_CUE_SUGGEST_NEAR_MISS_EPISODES  # noqa: E402


class TestNearMissStreakSuggestion(unittest.TestCase):
    def test_a_full_streak_of_near_misses_proposes_a_threshold(self):
        peaks = [(0.612, 0), (0.597, 0), (0.605, 0)]
        self.assertEqual(near_miss_streak_suggestion(0.750, peaks), 0.58)

    def test_one_real_match_breaks_the_streak(self):
        peaks = [(0.612, 0), (0.597, 1), (0.605, 0)]
        self.assertIsNone(near_miss_streak_suggestion(0.750, peaks))

    def test_a_silent_episode_breaks_the_streak(self):
        peaks = [(0.612, 0), (None, 0), (0.605, 0)]
        self.assertIsNone(near_miss_streak_suggestion(0.750, peaks))

    def test_a_peak_down_in_the_noise_breaks_the_streak(self):
        peaks = [(0.612, 0), (0.310, 0), (0.605, 0)]
        self.assertIsNone(near_miss_streak_suggestion(0.750, peaks))

    def test_too_few_episodes_proposes_nothing(self):
        self.assertIsNone(near_miss_streak_suggestion(0.750, [(0.612, 0), (0.597, 0)]))

    def test_only_the_newest_episodes_count(self):
        peaks = [(0.612, 0), (0.597, 0), (0.605, 0), (0.100, 0)]
        self.assertEqual(near_miss_streak_suggestion(0.750, peaks), 0.58)

    def test_no_threshold_proposes_nothing(self):
        self.assertIsNone(near_miss_streak_suggestion(None, [(0.6, 0)] * 3))


class TestPipelineWarning(unittest.TestCase):
    def _run(self, templates, peaks):
        result = AudioAnalysisResult()
        result.cue_templates_debug = templates
        with patch.object(processing.db, 'cue_template_episode_peaks',
                          return_value={4: peaks}) as query, \
             self.assertLogs('podcast.audio', level=logging.WARNING) as logs:
            suggestions = processing._suggest_cue_thresholds(
                'example-podcast', 'a1b2c3d4e5f6', 7, result)
        return query, logs.output, suggestions

    def test_a_quiet_near_miss_template_is_reported_with_a_value(self):
        query, output, suggestions = self._run(
            [{'id': 4, 'label': 'Outro sting', 'match_count': 0,
              'eff_threshold': 0.750}],
            [(0.612, 0), (0.597, 0), (0.605, 0)])

        query.assert_called_once_with(7, [4], AUDIO_CUE_SUGGEST_NEAR_MISS_EPISODES)
        self.assertEqual(len(output), 1)
        self.assertIn('0.58', output[0])
        self.assertIn('Outro sting', output[0])
        # Handed to the Cue Template Quiet alert so it names the fix.
        self.assertEqual(suggestions, {4: 0.58})

    def test_a_template_that_matched_is_not_queried(self):
        result = AudioAnalysisResult()
        result.cue_templates_debug = [
            {'id': 4, 'label': 'Outro sting', 'match_count': 2,
             'eff_threshold': 0.750}]
        with patch.object(processing.db, 'cue_template_episode_peaks') as query:
            processing._suggest_cue_thresholds(
                'example-podcast', 'a1b2c3d4e5f6', 7, result)
        query.assert_not_called()

    def test_a_query_failure_is_not_fatal(self):
        result = AudioAnalysisResult()
        result.cue_templates_debug = [
            {'id': 4, 'label': 'Outro sting', 'match_count': 0,
             'eff_threshold': 0.750}]
        with patch.object(processing.db, 'cue_template_episode_peaks',
                          side_effect=RuntimeError('no such column')):
            processing._suggest_cue_thresholds(
                'example-podcast', 'a1b2c3d4e5f6', 7, result)


class TestEpisodePeaksQuery:
    """On temp_db, not the session database: the feeds these create would
    otherwise outlive the module and land in another test's query."""

    def test_newest_episode_first_with_match_counts(self, temp_db):
        temp_db.create_podcast('example-podcast', 'https://example.com/rss',
                               'Example Show')
        podcast_id = temp_db.get_podcast_by_slug('example-podcast')['id']
        for episode_id, near_miss, matched in (
                ('ep-old', 0.605, False), ('ep-mid', 0.597, False),
                ('ep-new', 0.612, True)):
            records = [{'template_id': 4, 'start_s': 1.0, 'end_s': 1.5,
                        'match_score': near_miss, 'outcome': 'below_threshold'}]
            if matched:
                records.append({'template_id': 4, 'start_s': 9.0, 'end_s': 9.5,
                                'match_score': 0.8, 'outcome': 'snap'})
            temp_db.record_cue_detections(podcast_id, episode_id, records)

        peaks = temp_db.cue_template_episode_peaks(podcast_id, [4], 3)[4]

        assert len(peaks) == 3
        assert sorted(p[0] for p in peaks) == [0.597, 0.605, 0.612]
        assert sorted(p[1] for p in peaks) == [0, 0, 1]

    def test_a_template_absent_from_an_episode_reads_as_silent(self, temp_db):
        temp_db.create_podcast('quiet-feed', 'https://example.com/rss',
                               'Example Show')
        podcast_id = temp_db.get_podcast_by_slug('quiet-feed')['id']
        temp_db.record_cue_detections(podcast_id, 'ep-1', [
            {'template_id': 4, 'start_s': 1.0, 'end_s': 1.5,
             'match_score': 0.6, 'outcome': 'below_threshold'}])
        temp_db.record_cue_detections(podcast_id, 'ep-2', [
            {'template_id': 9, 'start_s': 1.0, 'end_s': 1.5,
             'match_score': 0.6, 'outcome': 'below_threshold'}])

        peaks = temp_db.cue_template_episode_peaks(podcast_id, [4, 9], 2)

        assert sorted(peaks) == [4, 9]
        assert set(peaks[4]) == {(None, 0), (0.6, 0)}
        assert set(peaks[9]) == {(None, 0), (0.6, 0)}


class TestTheSuggestionIsBuiltWhereItIsUsed(unittest.TestCase):
    """The Cue Template Quiet alert is the only consumer and fires for a
    cue_only feed; detection ran the peaks query for every feed with
    templates and threw the answer away."""

    def test_first_pass_detection_does_not_query_episode_peaks(self):
        analysis = AudioAnalysisResult()
        analysis.cue_templates_debug = [
            {'id': 4, 'label': 'Outro sting', 'match_count': 0,
             'eff_threshold': 0.750}]
        ctx = types.SimpleNamespace(slug='example-podcast',
                                    episode_id='a1b2c3d4e5f6', podcast_id=7)

        with patch.object(processing, 'db') as db, \
             patch.object(processing.ad_detector, 'process_transcript',
                          return_value={'status': 'success', 'ads': []}), \
             patch.object(processing.storage, 'save_ads_json'), \
             patch.object(processing.status_service, 'update_job_stage'), \
             patch.object(processing, 'clear_fallback'):
            processing._detect_ads_first_pass(
                ctx, segments=[], audio_path='x.mp3', skip_patterns=False,
                audio_analysis_result=analysis, progress_callback=None)

        db.cue_template_episode_peaks.assert_not_called()


if __name__ == '__main__':
    unittest.main()
