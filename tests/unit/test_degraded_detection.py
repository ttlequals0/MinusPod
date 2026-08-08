"""Tests for degraded pass-1 completion (partial detection).

When pass-1 LLM detection fails entirely but pattern/fingerprint/cross-fetch
markers were already gathered, a transient, non-auth failure publishes those
markers instead of failing the episode outright (_detect_ads_first_pass).
The episode row records the degradation (detection_degraded), a clean rerun
clears it (_persist_episode_state), and exactly one automatic low-priority
llm re-detect is queued on the transition into degraded
(_maybe_enqueue_degraded_redetect).
"""
import os
import sys
import tempfile
import time
from contextlib import ExitStack
from unittest.mock import ANY, MagicMock, patch

os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='degraded_test_'))
os.environ.setdefault('SECRET_KEY', 'test-secret')

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

import pytest

import main_app.processing as processing
from main_app.episode_context import EpisodeContext
from llm_client import LimitExceededError
from utils.errors import ServiceUnavailableError

SEGMENTS = [{'start': 0.0, 'end': 5.0, 'text': 'hello'}]
PATTERN_ADS = [{'start': 10.0, 'end': 20.0, 'confidence': 0.9,
                'detection_stage': 'text_pattern'}]


def _call_detect(ad_result, run_stats=None):
    """Invoke _detect_ads_first_pass with ad_detector/db/storage stubbed."""
    ctx = EpisodeContext(slug='degraded-feed', episode_id='ep1', podcast_id='1')
    with ExitStack() as stack:
        p = lambda *a, **k: stack.enter_context(patch.object(*a, **k))
        ad_detector = p(processing, 'ad_detector')
        db = p(processing, 'db')
        p(processing, 'storage')
        p(processing, 'status_service')
        ad_detector.process_transcript.return_value = ad_result
        result = processing._detect_ads_first_pass(
            ctx, SEGMENTS, '/tmp/ep.mp3', skip_patterns=False,
            audio_analysis_result=None, progress_callback=None,
            run_stats=run_stats,
        )
    return result, db


class TestDegradedContinue:
    def test_transient_failure_with_markers_returns_stage_markers(self):
        ad_result = {'status': 'failed', 'error': 'Overloaded (server busy)',
                     'ads': list(PATTERN_ADS), 'detection_stats': {}}
        run_stats = {}
        (first_pass_ads, count, returned_result), db = _call_detect(ad_result, run_stats)

        assert first_pass_ads == PATTERN_ADS
        assert count == 1
        assert returned_result is ad_result
        assert run_stats['detection_degraded'] == 'Overloaded (server busy)'
        db.upsert_episode.assert_any_call(
            'degraded-feed', 'ep1', ad_detection_status='failed',
            detection_degraded='Overloaded (server busy)')

    def test_zero_markers_still_hard_fails(self):
        ad_result = {'status': 'failed', 'error': 'Overloaded (server busy)',
                     'ads': [], 'detection_stats': {}}
        with pytest.raises(Exception, match='Ad detection failed'):
            _call_detect(ad_result, {})

    def test_service_unavailable_still_raises_even_with_markers(self):
        ad_result = {'status': 'failed', 'error': 'Connection refused',
                     'ads': list(PATTERN_ADS), 'connectivity': True,
                     'detection_stats': {}}
        with pytest.raises(ServiceUnavailableError):
            _call_detect(ad_result, {})

    def test_limit_exceeded_still_raises_even_with_markers(self):
        ad_result = {'status': 'failed', 'error': 'Key limit exceeded (monthly limit)',
                     'ads': list(PATTERN_ADS), 'limit_exceeded': True,
                     'detection_stats': {}}
        with pytest.raises(LimitExceededError):
            _call_detect(ad_result, {})

    def test_auth_class_failure_defers_does_not_degrade(self):
        ad_result = {'status': 'failed',
                     'error': 'authentication_error: invalid x-api-key',
                     'ads': list(PATTERN_ADS), 'detection_stats': {}}
        run_stats = {}
        with pytest.raises(Exception, match='Ad detection failed'):
            _call_detect(ad_result, run_stats)
        assert 'detection_degraded' not in run_stats

    def test_permanent_non_auth_error_with_markers_still_hard_fails(self):
        # is_transient_error classifies OOM as permanent; degrade must not fire.
        ad_result = {'status': 'failed', 'error': 'CUDA out of memory',
                     'ads': list(PATTERN_ADS), 'detection_stats': {}}
        run_stats = {}
        with pytest.raises(Exception, match='Ad detection failed'):
            _call_detect(ad_result, run_stats)
        assert 'detection_degraded' not in run_stats


class TestPersistEpisodeStateClearsFlag:
    def _call(self, detection_degraded=None):
        with ExitStack() as stack:
            p = lambda *a, **k: stack.enter_context(patch.object(*a, **k))
            db = p(processing, 'db')
            storage = p(processing, 'storage')
            mock_path = MagicMock()
            mock_path.exists.return_value = False
            storage.get_original_path.return_value = mock_path

            processing._persist_episode_state(
                'degraded-feed', 'ep1', pass1_cut_count=1, verification_count=0,
                first_pass_count=1, original_duration=100.0, new_duration=90.0,
                processed_version=1, detection_degraded=detection_degraded)
        return db

    def test_clean_run_clears_detection_degraded(self):
        db = self._call(detection_degraded=None)
        _, kwargs = db.upsert_episode.call_args
        assert kwargs['detection_degraded'] is None

    def test_degraded_run_persists_its_own_reason(self):
        db = self._call(detection_degraded='Overloaded (server busy)')
        _, kwargs = db.upsert_episode.call_args
        assert kwargs['detection_degraded'] == 'Overloaded (server busy)'


class TestFinalizeEpisodeComposesPersistDegradedFlag:
    """_finalize_episode is the sole caller of _persist_episode_state; this
    covers that composition end to end (the bug: an unconditional None write
    in _persist_episode_state clobbered the flag the SAME run had just set,
    so it never survived past the run that produced it)."""

    def _call_finalize(self, run_stats):
        with ExitStack() as stack:
            p = lambda *a, **k: stack.enter_context(patch.object(*a, **k))
            db = p(processing, 'db')
            storage = p(processing, 'storage')
            mock_path = MagicMock()
            mock_path.exists.return_value = False
            storage.get_original_path.return_value = mock_path
            p(processing, '_refresh_rss_for_slug')
            p(processing, '_log_completion_summary')
            p(processing, '_record_history_and_event')

            processing._finalize_episode(
                'degraded-feed', 'ep1', 'Ep Title', 'Podcast',
                pass1_cut_count=1, verification_count=0, first_pass_count=1,
                original_duration=100.0, new_duration=90.0, start_time=0.0,
                processed_version=1, run_stats=run_stats)
        return db

    def test_degraded_publish_persists_detection_degraded(self):
        db = self._call_finalize({'detection_degraded': 'Overloaded (server busy)'})
        _, kwargs = db.upsert_episode.call_args
        assert kwargs['detection_degraded'] == 'Overloaded (server busy)'

    def test_subsequent_clean_run_clears_it(self):
        db = self._call_finalize({})
        _, kwargs = db.upsert_episode.call_args
        assert kwargs['detection_degraded'] is None


class TestMaybeEnqueueDegradedRedetect:
    def _call(self, run_stats, episode_data):
        with ExitStack() as stack:
            p = lambda *a, **k: stack.enter_context(patch.object(*a, **k))
            db = p(processing, 'db')
            processing._maybe_enqueue_degraded_redetect(
                'degraded-feed', 'ep1', 'https://example.com/ep1.mp3',
                'Ep Title', 'Podcast', 'desc', '2026-01-01T00:00:00Z',
                episode_data, run_stats)
        return db

    def test_enqueues_once_on_transition_into_degraded(self):
        db = self._call({'detection_degraded': 'boom'},
                        {'detection_degraded': None})
        db.upsert_episode.assert_called_once_with(
            'degraded-feed', 'ep1', reprocess_mode='llm',
            reprocess_requested_at=ANY)
        db.upsert_episode_for_processing.assert_called_once_with(
            'degraded-feed', 'ep1', 'https://example.com/ep1.mp3', 'Ep Title',
            '2026-01-01T00:00:00Z', 'desc', priority=-10)

    def test_does_not_enqueue_when_already_degraded_before_this_run(self):
        # The automatic re-detect itself degrading again must not re-queue.
        db = self._call({'detection_degraded': 'boom again'},
                        {'detection_degraded': 'already set'})
        db.upsert_episode.assert_not_called()
        db.upsert_episode_for_processing.assert_not_called()

    def test_does_not_enqueue_when_run_is_not_degraded(self):
        db = self._call({}, {'detection_degraded': None})
        db.upsert_episode.assert_not_called()
        db.upsert_episode_for_processing.assert_not_called()

    def test_does_not_enqueue_when_episode_data_is_none(self):
        db = self._call({'detection_degraded': 'boom'}, None)
        db.upsert_episode.assert_called_once()
        db.upsert_episode_for_processing.assert_called_once()


class TestRecutPreservesDegradedFlag:
    """A recut only re-cuts existing markers -- detection never runs -- so it
    must not clear a detection_degraded flag it had no part in setting."""

    def _run_recut(self, episode_data, run_stats=None):
        with ExitStack() as stack:
            p = lambda *a, **k: stack.enter_context(patch.object(*a, **k))
            db = p(processing, 'db')
            storage = p(processing, 'storage')
            p(processing, 'status_service')
            p(processing, '_copy_retained_original_to_temp',
              return_value='/tmp/degraded-recut-work.mp3')
            p(processing, '_build_recut_ad_list', return_value=([], []))
            p(processing, '_generate_assets')
            finalize = p(processing, '_finalize_episode')
            local_ap_cls = p(processing, 'AudioProcessor')
            p(processing.os.path, 'exists', return_value=False)
            p(processing.shutil, 'move')

            db.get_episode.return_value = episode_data
            db.get_original_segments.return_value = [{'start': 0.0, 'end': 60.0}]
            db.get_all_settings.return_value = {}
            db.resolve_segment_actions.return_value = {}
            storage.get_original_path.return_value.exists.return_value = True
            storage.get_applied_cuts.return_value = None
            storage.get_episode_path.return_value = '/tmp/degraded-recut-final.mp3'

            local_ap = local_ap_cls.return_value
            local_ap.get_audio_duration.return_value = 60.0
            local_ap.process_episode.return_value = ('/tmp/degraded-recut-cut.mp3', [])

            processing._recut_episode(
                'degraded-feed', 'ep1', 'Episode', 'Podcast', 'desc',
                time.time(), cancel_event=None, run_stats=run_stats)
        return finalize

    def test_standalone_recut_preserves_existing_degraded_flag(self):
        # No run_stats: the standalone/bulk recut entrypoint (rerender-segments
        # API, reprocess mode=recut) never ran detection this call.
        episode_data = {'podcast_id': 1, 'processed_version': 0,
                        'detection_degraded': 'Overloaded (server busy)'}
        finalize = self._run_recut(episode_data, run_stats=None)
        _, kwargs = finalize.call_args
        assert kwargs['run_stats'] == {'detection_degraded': 'Overloaded (server busy)'}

    def test_standalone_recut_stays_clean_when_not_previously_degraded(self):
        episode_data = {'podcast_id': 1, 'processed_version': 0,
                        'detection_degraded': None}
        finalize = self._run_recut(episode_data, run_stats=None)
        _, kwargs = finalize.call_args
        assert kwargs['run_stats'] is None

    def test_corroborated_hold_recut_forwards_its_own_run_stats_unchanged(self):
        # A recut folded into an active pipeline run passes that run's real
        # run_stats through untouched, even if it differs from the stale
        # on-disk flag (this run is the authority on its own outcome).
        episode_data = {'podcast_id': 1, 'processed_version': 0,
                        'detection_degraded': 'stale reason on disk'}
        run_stats = {'detection_degraded': 'fresh reason from this run'}
        finalize = self._run_recut(episode_data, run_stats=run_stats)
        _, kwargs = finalize.call_args
        assert kwargs['run_stats'] is run_stats
