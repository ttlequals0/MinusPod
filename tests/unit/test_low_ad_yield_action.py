"""Tests for the low-ad-yield response policy: the shared heuristic, the
global/per-feed action resolution, and the pipeline hook that reruns
detection once per episode."""
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

from tests.app_bootstrap import bootstrap

_data_dir = bootstrap('low_ad_yield_test_')

from ad_yield import low_ad_yield  # noqa: E402
from config import (  # noqa: E402
    LOW_AD_YIELD_ACTIONS, LOW_AD_YIELD_ACTION_MODES,
    resolve_low_ad_yield_action,
)
from database import Database  # noqa: E402
import main_app.processing as processing  # noqa: E402
from reprocess_modes import clear_episode_for_mode  # noqa: E402
from utils.constants import REPROCESS_SOURCE_JIT  # noqa: E402


def _runs(**stats):
    """One completed run carrying the pipeline's raw stats blob."""
    return [{'status': 'completed', 'stats': stats}]


def _db(yields):
    db = MagicMock()
    db.get_recent_ad_yields.return_value = yields
    return db


class TestLowAdYieldHeuristic:
    """Feed-relative comparison, moved out of api/episodes.py unchanged."""

    def test_flags_a_run_far_below_the_feed_average(self):
        episode = {'podcast_id': 1, 'episode_id': 'ep1',
                   'original_duration': 3600.0, 'new_duration': 3600.0}
        result = low_ad_yield(_db([600.0, 620.0, 580.0]), episode, _runs(mode='auto'))
        assert result == {'removedSeconds': 0.0, 'feedAverageSeconds': 600.0,
                          'sampleSize': 3}

    def test_no_flag_when_yield_is_normal(self):
        episode = {'podcast_id': 1, 'episode_id': 'ep1',
                   'original_duration': 3600.0, 'new_duration': 3000.0}
        assert low_ad_yield(_db([600.0, 620.0, 580.0]), episode, _runs()) is None

    def test_no_flag_below_min_samples(self):
        episode = {'podcast_id': 1, 'episode_id': 'ep1',
                   'original_duration': 3600.0, 'new_duration': 3600.0}
        assert low_ad_yield(_db([600.0, 620.0]), episode, _runs()) is None

    def test_no_flag_when_feed_average_is_small(self):
        episode = {'podcast_id': 1, 'episode_id': 'ep1',
                   'original_duration': 3600.0, 'new_duration': 3600.0}
        assert low_ad_yield(_db([30.0, 40.0, 20.0]), episode, _runs()) is None

    def test_suppressed_for_passthrough_run(self):
        episode = {'podcast_id': 1, 'episode_id': 'ep1',
                   'original_duration': 3600.0, 'new_duration': 3600.0}
        assert low_ad_yield(_db([600.0, 620.0, 580.0]), episode,
                            _runs(mode='passthrough')) is None

    def test_suppressed_for_skip_detection_run_either_casing(self):
        episode = {'podcast_id': 1, 'episode_id': 'ep1',
                   'original_duration': 3600.0, 'new_duration': 3600.0}
        db = _db([600.0, 620.0, 580.0])
        assert low_ad_yield(db, episode, _runs(detection_skipped=True)) is None
        assert low_ad_yield(db, episode, _runs(detectionSkipped=True)) is None

    def test_no_flag_without_durations(self):
        episode = {'podcast_id': 1, 'episode_id': 'ep1',
                   'original_duration': None, 'new_duration': None}
        assert low_ad_yield(_db([600.0, 620.0, 580.0]), episode, _runs()) is None


class TestResolveLowAdYieldAction:
    """Per-feed override wins over the global setting."""

    def setup_method(self):
        self.db = Database()
        self.db.set_setting('low_ad_yield_action', 'nothing', is_default=True)

    def test_defaults_to_nothing(self):
        self.db.clear_setting('low_ad_yield_action')
        assert resolve_low_ad_yield_action(self.db, {}) == 'nothing'

    def test_global_value_applies_when_feed_is_unset(self):
        self.db.set_setting('low_ad_yield_action', 'redetect', is_default=False)
        assert resolve_low_ad_yield_action(self.db, {}) == 'redetect'
        assert resolve_low_ad_yield_action(
            self.db, {'low_ad_yield_action': None}) == 'redetect'

    def test_feed_override_wins(self):
        self.db.set_setting('low_ad_yield_action', 'redetect', is_default=False)
        assert resolve_low_ad_yield_action(
            self.db, {'low_ad_yield_action': 'full'}) == 'full'

    def test_feed_override_can_turn_the_policy_off(self):
        self.db.set_setting('low_ad_yield_action', 'full', is_default=False)
        assert resolve_low_ad_yield_action(
            self.db, {'low_ad_yield_action': 'nothing'}) == 'nothing'

    def test_unknown_values_fall_back_to_nothing(self):
        self.db.set_setting('low_ad_yield_action', 'bogus', is_default=False)
        assert resolve_low_ad_yield_action(self.db, {}) == 'nothing'
        assert resolve_low_ad_yield_action(
            self.db, {'low_ad_yield_action': 'bogus'}) == 'nothing'

    def test_every_action_maps_to_a_reprocess_mode(self):
        assert set(LOW_AD_YIELD_ACTIONS) == {'nothing', 'redetect', 'reprocess', 'full'}
        assert LOW_AD_YIELD_ACTION_MODES == {
            'redetect': 'llm', 'reprocess': 'reprocess', 'full': 'full'}


class TestSchemaColumns:
    """Both override columns exist after migration."""

    def test_columns_exist(self):
        db = Database()
        conn = db.get_connection()
        ep_cols = {r['name'] for r in conn.execute('PRAGMA table_info(episodes)')}
        pod_cols = {r['name'] for r in conn.execute('PRAGMA table_info(podcasts)')}
        assert 'low_yield_rerun_at' in ep_cols
        assert 'low_ad_yield_action' in pod_cols


class TestLowAdYieldAgainstRealHistory:
    """The motivating shape: a feed that usually loses about 10 minutes of ads
    and one episode that lost nothing."""

    def _seed(self, db, slug):
        db.create_podcast(slug, 'https://example.com/feed.xml', 'A Podcast')
        for i in range(3):
            episode_id = f'baseline-{i}'
            db.upsert_episode(slug, episode_id, original_url='https://example.com/a.mp3',
                              status='processed', original_duration=3600.0,
                              new_duration=3000.0,
                              processed_at=f'2026-08-0{i + 1}T00:00:00Z')
        db.upsert_episode(slug, 'flat-copy', original_url='https://example.com/b.mp3',
                          status='processed', original_duration=3600.0,
                          new_duration=3600.0, processed_at='2026-08-04T00:00:00Z')

    def test_flags_the_episode_that_removed_nothing(self):
        db = Database()
        slug = 'low-yield-fixture-feed'
        self._seed(db, slug)
        episode = db.get_episode(slug, 'flat-copy')

        result = low_ad_yield(db, episode, _runs(mode='auto'))
        assert result == {'removedSeconds': 0.0, 'feedAverageSeconds': 600.0,
                          'sampleSize': 3}

        db.delete_podcast(slug)


SEGMENTS = [{'start': 0.0, 'end': 5.0, 'text': 'hello'}]


class TestCompletionPathWiring:
    """The gate matrix is worthless if the pipeline never calls the hook."""

    def _run_pipeline(self, episode_row=None):
        podcast_row = {'id': 1, 'slug': 'wiring-feed', 'description': None,
                       'tags': None, 'dai_platform': None,
                       'passthrough_enabled': None, 'skip_ad_detection': None}
        with ExitStack() as stack:
            p = lambda *a, **k: stack.enter_context(patch.object(*a, **k))  # noqa: E731
            db = p(processing, 'db')
            p(processing, 'status_service')
            storage = p(processing, 'storage')
            audio_processor = p(processing, 'audio_processor')
            p(processing.ad_detector, 'get_model', return_value='test-model')
            p(processing.ad_detector, 'get_verification_model', return_value='test-model')
            p(processing, 'start_episode_token_tracking')
            p(processing, 'get_available_memory_gb', return_value=None)
            p(processing, 'get_min_cut_confidence', return_value=0.8)
            p(processing, '_download_and_transcribe',
              return_value=('/tmp/wiring.mp3', SEGMENTS))
            p(processing, '_run_differential_fetch', return_value=None)
            p(processing, '_run_audio_analysis', return_value=None)
            p(processing, 'load_positional_prior', return_value=None)
            p(processing, '_detect_ads_first_pass', return_value=([], 0, None))
            p(processing, '_refine_and_validate', return_value=([], []))
            p(processing, '_run_ad_reviewer', return_value=([], []))
            p(processing, '_snap_terminal_starts', return_value=[])
            p(processing, '_complete_cut_tails', return_value=[])
            local_ap_cls = p(processing, 'AudioProcessor')
            p(processing, '_run_verification_pass',
              return_value=(0, [], [], [], '/tmp/cut.mp3', 0, True, 0))
            p(processing, '_generate_assets')
            finalize = p(processing, '_finalize_episode')
            hook = p(processing, '_maybe_fire_low_ad_yield_action')
            p(processing.shutil, 'move')
            p(processing.os, 'unlink')
            p(processing.os.path, 'exists', return_value=False)

            db.get_episode.return_value = dict(
                episode_row or {'reprocess_requested_at': None})
            db.get_podcast_by_slug.return_value = podcast_row
            db.get_setting.return_value = 'false'
            db.get_all_settings.return_value = {}
            audio_processor.get_audio_duration.return_value = 100.0
            local_ap = local_ap_cls.return_value
            local_ap.process_episode.return_value = ('/tmp/cut.mp3', [])
            local_ap.get_audio_duration.return_value = 100.0
            storage.get_episode_path.return_value = '/tmp/final.mp3'
            result = processing.process_episode(
                'wiring-feed', 'ep1', 'https://example.com/ep1.mp3')
        return result, finalize, hook, db

    def test_a_policy_rerun_clears_when_it_starts_processing(self):
        _, _, _, db = self._run_pipeline(
            episode_row={'reprocess_mode': 'reprocess', 'reprocess_source': 'policy',
                         'reprocess_requested_at': '2026-01-01T00:00:00Z'})

        db.clear_episode_details.assert_called_once_with('wiring-feed', 'ep1')

    def test_an_ordinary_run_clears_nothing_up_front(self):
        _, _, _, db = self._run_pipeline()

        db.clear_episode_details.assert_not_called()
        db.clear_episode_ad_data.assert_not_called()

    def test_the_success_path_calls_the_hook_after_finalize(self):
        result, finalize, hook, _ = self._run_pipeline()

        assert result is True
        finalize.assert_called_once()
        hook.assert_called_once()
        args = hook.call_args.args
        assert args[0] == 'wiring-feed'
        assert args[1] == 'ep1'
        # Last two arguments are the pre-run row snapshot and this run's stats.
        assert args[-1]['mode'] == 'auto'


class TestReprocessProvenance:
    """reprocess_source describes the reprocess_requested_at stamp it was
    written with; an unannotated write must not inherit a stale marker."""

    def _seed(self, db, slug, episode_id):
        db.create_podcast(slug, 'https://example.com/feed.xml', 'A Podcast')
        db.upsert_episode(slug, episode_id, original_url='https://example.com/a.mp3',
                          reprocess_requested_at='2026-01-01T00:00:00Z',
                          reprocess_source=REPROCESS_SOURCE_JIT)

    def test_jit_marker_round_trips(self):
        db = Database()
        slug = 'provenance-jit-feed'
        self._seed(db, slug, 'ep1')
        assert db.get_episode(slug, 'ep1')['reprocess_source'] == REPROCESS_SOURCE_JIT
        db.delete_podcast(slug)

    def test_a_later_unannotated_stamp_clears_the_marker(self):
        db = Database()
        slug = 'provenance-manual-feed'
        self._seed(db, slug, 'ep1')
        db.upsert_episode(slug, 'ep1', status='pending',
                          reprocess_requested_at='2026-02-01T00:00:00Z')
        assert db.get_episode(slug, 'ep1')['reprocess_source'] is None
        db.delete_podcast(slug)

    def test_batch_pending_clears_the_marker(self):
        db = Database()
        slug = 'provenance-batch-feed'
        self._seed(db, slug, 'ep1')
        db.batch_set_episodes_pending(slug, ['ep1'], reprocess_mode='full',
                                      reprocess_requested_at='2026-02-01T00:00:00Z')
        assert db.get_episode(slug, 'ep1')['reprocess_source'] is None
        db.delete_podcast(slug)

    def test_an_unrelated_write_keeps_the_marker(self):
        db = Database()
        slug = 'provenance-untouched-feed'
        self._seed(db, slug, 'ep1')
        db.upsert_episode(slug, 'ep1', status='processing')
        assert db.get_episode(slug, 'ep1')['reprocess_source'] == REPROCESS_SOURCE_JIT
        db.delete_podcast(slug)


class TestClearEpisodeForMode:
    """The per-mode clearing rules now live in reprocess_modes, imported by
    both the API and the pipeline."""

    def test_llm_keeps_the_transcript(self):
        db = MagicMock()
        clear_episode_for_mode(db, 'a-feed', 'ep1', 'llm')
        db.clear_episode_ad_data.assert_called_once_with('a-feed', 'ep1')
        db.clear_episode_details.assert_not_called()

    def test_reprocess_and_full_wipe_the_details_row(self):
        for mode in ('reprocess', 'full'):
            db = MagicMock()
            clear_episode_for_mode(db, 'a-feed', 'ep1', mode)
            db.clear_episode_details.assert_called_once_with('a-feed', 'ep1')

    def test_recut_clears_nothing(self):
        db = MagicMock()
        clear_episode_for_mode(db, 'a-feed', 'ep1', 'recut')
        db.clear_episode_details.assert_not_called()
        db.clear_episode_ad_data.assert_not_called()



class TestFireLowAdYieldAction:
    """Gate matrix and firing behavior for the pipeline hook."""

    EPISODE = {'podcast_id': 1, 'episode_id': 'ep1',
               'original_duration': 3600.0, 'new_duration': 3600.0}

    def _call(self, *, episode_data=None, episode=None, action='redetect',
              run_stats=None, yields=(600.0, 620.0, 580.0), has_transcript=True):
        with ExitStack() as stack:
            p = lambda *a, **k: stack.enter_context(patch.object(*a, **k))  # noqa: E731
            db = p(processing, 'db')
            status_service = p(processing, 'status_service')
            db.get_podcast_by_slug.return_value = {'id': 1, 'slug': 'a-feed',
                                                   'queue_priority': None}
            db.get_episode.return_value = dict(episode or self.EPISODE)
            db.get_recent_ad_yields.return_value = list(yields)
            db.has_transcript.return_value = has_transcript
            p(processing, 'resolve_low_ad_yield_action', return_value=action)
            processing._maybe_fire_low_ad_yield_action(
                'a-feed', 'ep1', 'https://example.com/ep1.mp3', 'Ep Title',
                'A Podcast', 'desc', '2026-01-01T00:00:00Z',
                episode_data if episode_data is not None else {},
                run_stats if run_stats is not None else {'mode': 'auto'})
        return db, status_service

    def _rerun_kwargs(self, db):
        return [c.kwargs for c in db.upsert_episode.call_args_list]

    def test_fires_on_a_pipeline_run_with_low_yield(self):
        db, status_service = self._call()
        kwargs = self._rerun_kwargs(db)
        assert kwargs[0] == {'low_yield_rerun_at': kwargs[0]['low_yield_rerun_at']}
        assert kwargs[0]['low_yield_rerun_at']
        assert kwargs[1]['reprocess_mode'] == 'llm'
        assert kwargs[1]['reprocess_requested_at']
        assert kwargs[1]['reprocess_source'] == 'policy'
        db.upsert_episode_for_processing.assert_called_once()
        status_service.queue_episode.assert_called_once()

    def test_the_episode_stays_published_until_the_rerun_starts(self):
        db, _ = self._call()
        for kwargs in self._rerun_kwargs(db):
            assert 'status' not in kwargs
        db.clear_episode_details.assert_not_called()
        db.clear_episode_ad_data.assert_not_called()

    def test_the_rerun_is_queued_behind_fresh_episodes(self):
        db, _ = self._call()
        assert db.upsert_episode_for_processing.call_args.kwargs['priority'] == -10

    def test_stamp_is_written_before_the_queue_row(self):
        db, _ = self._call()
        calls = [c[0] for c in db.method_calls
                 if c[0] in ('upsert_episode', 'upsert_episode_for_processing')]
        assert calls[0] == 'upsert_episode'
        assert calls[-1] == 'upsert_episode_for_processing'

    def test_reprocess_action_uses_reprocess_mode(self):
        db, _ = self._call(action='reprocess')
        assert self._rerun_kwargs(db)[1]['reprocess_mode'] == 'reprocess'

    def test_full_action_uses_full_mode(self):
        db, _ = self._call(action='full')
        assert self._rerun_kwargs(db)[1]['reprocess_mode'] == 'full'

    def test_redetect_without_transcript_falls_back_to_reprocess(self):
        db, _ = self._call(action='redetect', has_transcript=False)
        assert self._rerun_kwargs(db)[1]['reprocess_mode'] == 'reprocess'

    def test_jit_direct_run_fires(self):
        # A play request that started processing straight away leaves no stamp.
        db, _ = self._call(episode_data={})
        db.upsert_episode_for_processing.assert_called_once()

    def test_degraded_redetect_run_fires(self):
        # The degraded chain's own re-detect is pipeline work, so its clean
        # run is the first one the policy can judge.
        db, _ = self._call(episode_data={
            'reprocess_requested_at': '2026-01-01T00:00:00Z',
            'reprocess_source': 'degraded'})
        db.upsert_episode_for_processing.assert_called_once()

    def test_jit_queue_busy_run_fires(self):
        # The stamp only gets the play past the auto-process gate; the jit
        # marker says a listener request, not a person, asked for this run.
        db, _ = self._call(episode_data={
            'reprocess_requested_at': '2026-01-01T00:00:00Z',
            'reprocess_source': 'jit'})
        db.upsert_episode_for_processing.assert_called_once()

    def test_manual_run_does_not_fire(self):
        db, _ = self._call(
            episode_data={'reprocess_requested_at': '2026-01-01T00:00:00Z',
                          'reprocess_source': None})
        db.upsert_episode.assert_not_called()
        db.upsert_episode_for_processing.assert_not_called()

    def test_policy_rerun_does_not_fire(self):
        db, _ = self._call(
            episode_data={'reprocess_requested_at': '2026-01-01T00:00:00Z',
                          'reprocess_source': 'policy'})
        db.upsert_episode.assert_not_called()
        db.upsert_episode_for_processing.assert_not_called()

    def test_already_rerun_episode_does_not_fire(self):
        episode = dict(self.EPISODE, low_yield_rerun_at='2026-01-01T00:00:00Z')
        db, _ = self._call(episode=episode)
        db.upsert_episode.assert_not_called()

    def test_action_nothing_does_not_fire(self):
        db, _ = self._call(action='nothing')
        db.upsert_episode.assert_not_called()
        db.get_episode.assert_not_called()

    def test_degraded_run_does_not_fire(self):
        # The degraded re-detect owns that case; two hooks must not both queue.
        db, _ = self._call(run_stats={'mode': 'auto', 'detection_degraded': 'boom'})
        db.upsert_episode.assert_not_called()

    def test_cue_only_run_does_not_fire(self):
        # A rerun of a cue-only feed runs the identical pipeline, so it can
        # only burn the one shot.
        db, _ = self._call(run_stats={'mode': 'auto', 'cue_only': True})
        db.upsert_episode.assert_not_called()

    def test_passthrough_run_does_not_fire(self):
        db, _ = self._call(run_stats={'mode': 'passthrough'})
        db.upsert_episode.assert_not_called()

    def test_skip_detection_run_does_not_fire(self):
        db, _ = self._call(run_stats={'mode': 'auto', 'detection_skipped': True})
        db.upsert_episode.assert_not_called()

    def test_too_few_samples_does_not_fire(self):
        db, _ = self._call(yields=(600.0, 620.0))
        db.upsert_episode.assert_not_called()

    def test_normal_yield_does_not_fire(self):
        episode = dict(self.EPISODE, new_duration=3000.0)
        db, _ = self._call(episode=episode)
        db.upsert_episode.assert_not_called()

    def test_hook_swallows_exceptions(self):
        with ExitStack() as stack:
            p = lambda *a, **k: stack.enter_context(patch.object(*a, **k))  # noqa: E731
            db = p(processing, 'db')
            p(processing, 'status_service')
            db.get_podcast_by_slug.side_effect = RuntimeError('db is down')
            processing._maybe_fire_low_ad_yield_action(
                'a-feed', 'ep1', 'https://example.com/ep1.mp3', 'Ep Title',
                'A Podcast', 'desc', None, {}, {'mode': 'auto'})
