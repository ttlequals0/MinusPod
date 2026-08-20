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


import main_app.processing as processing  # noqa: E402


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
        assert kwargs[1]['status'] == 'pending'
        db.upsert_episode_for_processing.assert_called_once()
        status_service.queue_episode.assert_called_once()

    def test_stamp_is_written_before_the_queue_row(self):
        db, _ = self._call()
        calls = [c[0] for c in db.method_calls
                 if c[0] in ('upsert_episode', 'upsert_episode_for_processing')]
        assert calls[0] == 'upsert_episode'
        assert calls[-1] == 'upsert_episode_for_processing'

    def test_reprocess_action_uses_reprocess_mode(self):
        db, _ = self._call(action='reprocess')
        assert self._rerun_kwargs(db)[1]['reprocess_mode'] == 'reprocess'
        db.clear_episode_details.assert_called_once_with('a-feed', 'ep1')

    def test_full_action_uses_full_mode(self):
        db, _ = self._call(action='full')
        assert self._rerun_kwargs(db)[1]['reprocess_mode'] == 'full'

    def test_redetect_clears_ad_data_only(self):
        db, _ = self._call(action='redetect')
        db.clear_episode_ad_data.assert_called_once_with('a-feed', 'ep1')
        db.clear_episode_details.assert_not_called()

    def test_redetect_without_transcript_falls_back_to_reprocess(self):
        db, _ = self._call(action='redetect', has_transcript=False)
        assert self._rerun_kwargs(db)[1]['reprocess_mode'] == 'reprocess'

    def test_manual_run_does_not_fire(self):
        db, _ = self._call(
            episode_data={'reprocess_requested_at': '2026-01-01T00:00:00Z'})
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
