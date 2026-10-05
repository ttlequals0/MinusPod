"""Offline queue (#482): connectivity classification, deferral, TTL expiry,
and re-drive.

Uses the main_app boot pattern from test_history_ad_count: bind a temp
DATA_DIR before importing main_app so singletons initialize against it.
"""
import json
import socket
import time
from unittest.mock import MagicMock, call, patch

import pytest
import requests
import httpx
import openai

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('offline_queue_test_')
from llm_client import is_connectivity_error, LimitExceededError, StructuralRateLimitError
import failover
import llm_route
from ad_detector import _windows_failed_response
from config import MAX_EPISODE_RETRIES
from main_app import db
import main_app.background as background
from main_app.episode_context import EpisodeContext
from main_app.processing import _detect_ads_first_pass, _handle_processing_failure, is_transient_error
from utils import llm_call
from offline_queue import offline_queue_tick
from utils.circuit_breaker import CircuitBreakerOpen
from utils.errors import (
    ServiceUnavailableError, AudioTooLargeError, LocalTranscriptionUnavailableError,
)


class TestIsConnectivityError:
    @pytest.mark.parametrize('error', [
        CircuitBreakerOpen('llm-api', 42.0),
        requests.exceptions.ConnectionError('refused'),
        requests.exceptions.Timeout('timed out'),
        ConnectionError('refused'),
        TimeoutError('timed out'),
        socket.gaierror('dns failure'),
    ])
    def test_connectivity_errors_true(self, error):
        assert is_connectivity_error(error) is True

    @pytest.mark.parametrize('error', [
        StructuralRateLimitError('per-minute cap exceeded'),
        Exception('rate limit reached (429)'),
        Exception('model not found'),
        ValueError('bad value'),
        FileNotFoundError('missing'),
    ])
    def test_non_connectivity_errors_false(self, error):
        assert is_connectivity_error(error) is False


SLUG = 'offline-queue-feed'


def _target_resolver(routes=None):
    routes = routes or {'llm': ['llm:primary'], 'whisper': ['whisper:active']}
    return lambda _db: lambda service, _episode: routes.get(service, [])


@pytest.fixture
def seeded_episode():
    db.create_podcast(SLUG, 'https://example.com/feed.xml', title='Offline Queue Test')
    db.upsert_episode(SLUG, 'ep-1', title='Episode 1', status='processing',
                      original_url='https://example.com/ep1.mp3', retry_count=1)
    yield 'ep-1'
    db.delete_podcast(SLUG)
    db.set_setting('offline_queue_enabled', 'false')


def _fail(episode_id, error):
    episode_data = db.get_episode(SLUG, episode_id)
    with patch('main_app.processing.status_service'):
        _handle_processing_failure(SLUG, episode_id, 'Episode 1', 'Offline Queue Test',
                                   episode_data, error, start_time=0.0)


class TestDeferral:
    def test_standby_outage_after_primary_rejection_preserves_final_retry(self, seeded_episode):
        db.set_setting('offline_queue_enabled', 'true')
        final_retry = MAX_EPISODE_RETRIES - 1
        db.upsert_episode(SLUG, seeded_episode, retry_count=final_retry)
        request = httpx.Request('POST', 'http://example.com')
        primary_error = openai.NotFoundError('missing model', response=httpx.Response(404, request=request), body=None)
        standby_error = openai.InternalServerError('unavailable', response=httpx.Response(503, request=request), body=None)
        primary = MagicMock(); primary.create_message.side_effect = primary_error
        standby = MagicMock(); standby.create_message.side_effect = standby_error
        route = llm_route.Route(
            phase='detection', provider_key='openai-compatible', model_id='standby-model',
            base_url='http://example.com/v1', slot='failover', credential_slot='failover')
        with patch.object(failover, 'is_configured', return_value=True), \
                patch.object(failover, 'trigger', return_value=True), \
                patch.object(llm_call, '_failover_route', return_value=route), \
                patch.object(llm_call, 'client_for_route', return_value=standby), \
                patch.object(llm_call, '_manual_rate_limit_error', return_value=None), \
                patch.object(llm_call, '_sleep_before_retry', return_value=True), \
                patch.object(llm_call, '_ledger_call_once', side_effect=lambda c, kw, m, **k: c.create_message(**kw)):
            response, error = llm_call.call_llm(
                llm_client=primary, model='active-model', system_prompt='s', prompt='p',
                llm_timeout=1, max_retries=0, max_tokens=10, slug=SLUG,
                episode_id=seeded_episode, call_label='detection', phase_key='detection',
                provider='openai-compatible', credential_slot='primary')
        assert response is None and error is standby_error
        failure = _windows_failed_response('detection', 1, 1, error, 'standby-model')
        ctx = EpisodeContext(slug=SLUG, episode_id=seeded_episode)
        with patch('main_app.processing.ad_detector.process_transcript', return_value=failure), \
                patch('main_app.processing.storage'), patch('main_app.processing.status_service'), \
                pytest.raises(ServiceUnavailableError) as caught:
            _detect_ads_first_pass(ctx, [], '/unused.mp3', skip_patterns=False,
                                   audio_analysis_result=None, progress_callback=None)
        _fail(seeded_episode, caught.value)
        episode = db.get_episode(SLUG, seeded_episode)
        assert episode['status'] == 'deferred'
        assert episode['deferred_service'] == 'llm'
        assert episode['retry_count'] == final_retry

    def test_service_unavailable_defers_when_enabled(self, seeded_episode):
        db.set_setting('offline_queue_enabled', 'true')
        _fail(seeded_episode, ServiceUnavailableError('whisper', 'unreachable'))
        episode = db.get_episode(SLUG, seeded_episode)
        assert episode['status'] == 'deferred'
        assert episode['deferred_service'] == 'whisper'
        assert episode['deferred_at']
        assert episode['retry_count'] == 1  # untouched
        assert 'Deferred' in episode['error_message']

    def test_circuit_breaker_open_defers_when_enabled(self, seeded_episode):
        db.set_setting('offline_queue_enabled', 'true')
        _fail(seeded_episode, CircuitBreakerOpen('llm-api', 42.0))
        episode = db.get_episode(SLUG, seeded_episode)
        assert episode['status'] == 'deferred'
        assert episode['deferred_service'] == 'llm'

    def test_disabled_keeps_transient_retry_path(self, seeded_episode):
        db.set_setting('offline_queue_enabled', 'false')
        _fail(seeded_episode, ServiceUnavailableError('llm', 'unreachable'))
        episode = db.get_episode(SLUG, seeded_episode)
        assert episode['status'] == 'failed'
        assert episode['retry_count'] == 2  # incremented as today
        assert not episode.get('deferred_at')

    def test_genuine_error_still_fails_when_enabled(self, seeded_episode):
        db.set_setting('offline_queue_enabled', 'true')
        _fail(seeded_episode, ValueError('bad audio'))
        episode = db.get_episode(SLUG, seeded_episode)
        assert episode['status'] == 'permanently_failed'
        assert not episode.get('deferred_at')

    def test_re_deferral_keeps_first_deferred_at(self, seeded_episode):
        """The TTL bounds total time in the deferred lifecycle: a flapping
        endpoint (probe up, calls failing) must not reset the clock."""
        db.set_setting('offline_queue_enabled', 'true')
        first = '2026-01-01T00:00:00Z'
        db.upsert_episode(SLUG, seeded_episode, deferred_at=first,
                          deferred_service='llm')
        _fail(seeded_episode, ServiceUnavailableError('llm', 'still down'))
        episode = db.get_episode(SLUG, seeded_episode)
        assert episode['status'] == 'deferred'
        assert episode['deferred_at'] == first


class TestLimitExceededEpisodeOutcome:
    """A wrapped LimitExceededError must fail the episode permanently: its
    message carries "429"/"RateLimitError" text that the string fallbacks
    would misread as a transient rate limit and re-queue forever (#491)."""

    WRAPPED = LimitExceededError(
        "Ad detection failed: All 5 detection windows failed "
        "(last error: RateLimitError, status=429: quota exhausted)"
    )

    def test_not_transient(self):
        assert is_transient_error(self.WRAPPED) is False

    def test_episode_goes_permanently_failed(self, seeded_episode):
        _fail(seeded_episode, self.WRAPPED)
        episode = db.get_episode(SLUG, seeded_episode)
        assert episode['status'] == 'permanently_failed'
        assert episode['retry_count'] == 1  # untouched: no retry ladder
        assert 'quota exhausted' in episode['error_message']


class TestAudioTooLargeEpisodeOutcome:
    """An oversized enclosure never shrinks; the episode must fail
    permanently with the actionable cap message (#493)."""

    TOO_LARGE = AudioTooLargeError(
        "Audio file is 620MB, over the 500MB download cap")

    def test_not_transient(self):
        assert is_transient_error(self.TOO_LARGE) is False

    def test_episode_goes_permanently_failed(self, seeded_episode):
        _fail(seeded_episode, self.TOO_LARGE)
        episode = db.get_episode(SLUG, seeded_episode)
        assert episode['status'] == 'permanently_failed'
        assert 'MAX_AUDIO_DOWNLOAD_MB' in episode['error_message']


class TestTtlAndRequeue:
    def _defer(self, episode_id, deferred_at, service='llm'):
        db.upsert_episode(SLUG, episode_id, title=episode_id, status='deferred',
                          original_url=f'https://example.com/{episode_id}.mp3',
                          error_message='Deferred (llm endpoint unreachable)',
                          deferred_at=deferred_at, deferred_service=service)

    def test_expire_deferred_episodes_ttl_boundary(self, seeded_episode):
        self._defer('ep-old', '2020-01-01T00:00:00Z')
        self._defer('ep-young', '2999-01-01T00:00:00Z')
        expired = db.expire_deferred_episodes(48)
        assert [e['episode_id'] for e in expired] == ['ep-old']
        old = db.get_episode(SLUG, 'ep-old')
        assert old['status'] == 'permanently_failed'
        assert 'Offline queue TTL expired after 48 hours' in old['error_message']
        assert old['deferred_at'] is None
        young = db.get_episode(SLUG, 'ep-young')
        assert young['status'] == 'deferred'

    def test_requeue_only_matching_service(self, seeded_episode):
        self._defer('ep-llm', '2999-01-01T00:00:00Z', service='llm')
        self._defer('ep-whisper', '2999-01-01T00:00:00Z', service='whisper')
        requeued = db.requeue_deferred_episodes({'llm'})
        assert requeued == 1
        episode = db.get_episode(SLUG, 'ep-llm')
        assert episode['status'] == 'pending'
        # deferred_at survives the re-drive so the TTL keeps ticking.
        assert episode['deferred_at'] == '2999-01-01T00:00:00Z'
        assert db.get_episode(SLUG, 'ep-whisper')['status'] == 'deferred'
        queued = db.get_next_queued_episode()
        assert queued and queued['episode_id'] == 'ep-llm'

    def test_requeue_skips_auto_process_disabled_feed(self, seeded_episode):
        """Without a user-initiated reprocess marker, a disabled feed's
        episode stays deferred (TTL-bounded) instead of being flipped to a
        pending state the claim-time gate would strand forever."""
        self._defer('ep-gated', '2999-01-01T00:00:00Z', service='llm')
        db.update_podcast(SLUG, auto_process_override='false')
        try:
            requeued = db.requeue_deferred_episodes({'llm'})
            assert requeued == 0
            assert db.get_episode(SLUG, 'ep-gated')['status'] == 'deferred'
        finally:
            db.update_podcast(SLUG, auto_process_override=None)

    def test_expired_episode_fires_history_and_webhook(self, seeded_episode):
        self._defer('ep-old', '2020-01-01T00:00:00Z')
        with patch('offline_queue.fire_event') as webhook, \
             patch.object(db, 'record_processing_history') as history:
            offline_queue_tick(db, _target_resolver())
        assert history.call_count == 1
        assert webhook.call_count == 1
        kwargs = webhook.call_args.kwargs
        assert kwargs['processing_time'] == 0.0
        assert kwargs['llm_cost'] == 0.0

    def test_tick_no_deferred_makes_no_probe_calls(self, seeded_episode):
        with patch('failover.ensure_fresh_probes') as probe:
            offline_queue_tick(db, _target_resolver())
            probe.assert_not_called()

    def test_tick_probe_false_requeues_nothing(self, seeded_episode):
        self._defer('ep-llm', '2999-01-01T00:00:00Z', service='llm')
        with patch('failover.ensure_fresh_probes'), \
                patch('failover.current_probe_state', return_value={'reachable': False}):
            offline_queue_tick(db, _target_resolver())
        assert db.get_episode(SLUG, 'ep-llm')['status'] == 'deferred'

    def test_tick_probe_true_requeues(self, seeded_episode):
        self._defer('ep-llm', '2999-01-01T00:00:00Z', service='llm')
        with patch('failover.ensure_fresh_probes'), \
                patch('failover.current_probe_state', return_value={'reachable': True}):
            offline_queue_tick(db, _target_resolver())
        assert db.get_episode(SLUG, 'ep-llm')['status'] == 'pending'

    def test_effective_route_probes_only_required_targets_and_requeues_per_episode(
            self, seeded_episode):
        self._defer('ep-primary', '2999-01-01T00:00:00Z', service='llm')
        self._defer('ep-secondary', '2999-01-01T00:00:00Z', service='llm')
        target_map = {
            'ep-primary': ['llm:primary'],
            'ep-secondary': ['llm:secondary'],
        }
        probe_results = {
            'llm:primary': {'reachable': True},
            'llm:secondary': {'reachable': False},
        }
        with patch('failover.ensure_fresh_probes') as ensure_fresh, \
                patch('failover.current_probe_state', side_effect=probe_results.__getitem__):
            offline_queue_tick(
                db,
                lambda _db: lambda _service, episode: target_map[episode['episode_id']])
        ensure_fresh.assert_called_once_with(['llm:primary', 'llm:secondary'])
        assert db.get_episode(SLUG, 'ep-primary')['status'] == 'pending'
        assert db.get_episode(SLUG, 'ep-secondary')['status'] == 'deferred'

    def test_changed_route_uses_fresh_standby_result(self, seeded_episode):
        self._defer('ep-flipped', '2999-01-01T00:00:00Z', service='llm')
        route_calls = 0

        def resolve(_service, _episode):
            nonlocal route_calls
            route_calls += 1
            return ['llm:primary'] if route_calls == 1 else ['llm:failover']

        with patch('failover.ensure_fresh_probes') as ensure_fresh, \
                patch('failover.current_probe_state',
                      return_value={'reachable': True}):
            offline_queue_tick(db, lambda _db: resolve)
        assert ensure_fresh.call_args_list == [
            call(['llm:primary']), call(['llm:failover']),
        ]
        assert db.get_episode(SLUG, 'ep-flipped')['status'] == 'pending'

    def test_stale_result_after_probe_timeout_does_not_requeue(self, seeded_episode):
        self._defer('ep-stale', '2999-01-01T00:00:00Z', service='llm')
        with patch('failover.ensure_fresh_probes'), \
                patch('failover.current_probe_state', return_value={'reachable': None}):
            offline_queue_tick(
                db,
                lambda _db: lambda _service, _episode: ['llm:primary'])
        assert db.get_episode(SLUG, 'ep-stale')['status'] == 'deferred'

    def test_changed_config_and_live_lease_do_not_requeue_stale_healthy_result(
            self, seeded_episode, preserve_setting):
        for key in ('provider_config_revision', 'failover_probe:llm:primary',
                    'failover_probe_lease:llm:primary'):
            preserve_setting(key)
        self._defer('ep-lease', '2999-01-01T00:00:00Z')
        db.set_setting('provider_config_revision', 'before', is_default=False)
        with patch('failover.probe_target',
                   return_value={'reachable': True, 'status': 200, 'detail': ''}):
            failover.probe_tick(db, ['llm:primary'])
        db.set_setting('provider_config_revision', 'after', is_default=False)
        db.set_setting(
            'failover_probe_lease:llm:primary',
            json.dumps({'token': 'other-worker', 'expires_at': time.time() + 60}),
            is_default=False,
        )
        with patch.object(failover, '_PROBE_WAIT_SECONDS', 0), \
                patch('failover.probe_target') as probe:
            offline_queue_tick(
                db,
                lambda _db: lambda _service, _episode: ['llm:primary'])
        probe.assert_not_called()
        assert db.get_episode(SLUG, 'ep-lease')['status'] == 'deferred'

    def test_recut_does_not_probe_unused_services(self, seeded_episode):
        self._defer('ep-recut', '2999-01-01T00:00:00Z')
        db.upsert_episode(SLUG, 'ep-recut', reprocess_mode='recut')
        episode = {'podcast_slug': SLUG, 'episode_id': 'ep-recut'}
        snapshot = {'detection': {'credential_slot': 'primary'}}
        with patch.object(background, '_resolve_route_snapshot', return_value=snapshot), \
                patch.object(background, '_active_phases_for_admission') as active_phases, \
                patch('failover.run_probe_targets') as run_targets:
            resolve = background._offline_queue_target_resolver(db)
            assert resolve('llm', episode) == []
            assert resolve('whisper', episode) == []
        active_phases.assert_not_called()
        run_targets.assert_not_called()

    def test_resolver_uses_only_enabled_llm_phase_slots(self, seeded_episode, preserve_setting):
        for key in ('enable_ad_review', 'secondary_provider_enabled'):
            preserve_setting(key)
        self._defer('ep-routed', '2999-01-01T00:00:00Z')
        db.update_podcast(SLUG, skip_second_pass=True, chapters_mode='off')
        db.set_setting('enable_ad_review', 'false', is_default=False)
        db.set_setting('secondary_provider_enabled', 'true', is_default=False)
        failover.invalidate_cache()
        snapshot = {
            'detection': {'credential_slot': 'primary'},
            'review': {'credential_slot': 'secondary'},
            'verification': {'credential_slot': 'secondary'},
            'chapters': {'credential_slot': 'secondary'},
        }
        with patch.object(background, '_resolve_route_snapshot', return_value=snapshot):
            resolve = background._offline_queue_target_resolver(db)
            assert resolve('llm', {
                'podcast_slug': SLUG, 'episode_id': 'ep-routed'}) == ['llm:primary']

    def test_offline_tick_probes_active_llm_standby(self, seeded_episode, preserve_setting):
        self._defer('ep-llm-standby', '2999-01-01T00:00:00Z')
        for key in ('failover_llm_enabled', 'failover_llm_provider',
                    'failover_llm_detection_model', 'failover_llm_base_url',
                    'failover_state:llm:primary', 'failover_generation:llm:primary',
                    'failover_probe:llm:failover', 'failover_probe_lease:llm:failover'):
            preserve_setting(key)
        db.set_setting('failover_llm_enabled', 'true', is_default=False)
        db.set_setting('failover_llm_provider', 'openai-compatible', is_default=False)
        db.set_setting('failover_llm_detection_model', 'standby-model', is_default=False)
        failover.invalidate_cache()
        episode = {'podcast_slug': SLUG, 'episode_id': 'ep-llm-standby'}
        snapshot = {
            'detection': {'credential_slot': 'primary'},
            'review': {'credential_slot': 'primary'},
            'verification': {'credential_slot': 'primary'},
            'chapters': {'credential_slot': 'primary'},
        }
        with patch.object(background, '_resolve_route_snapshot', return_value=snapshot), \
                patch('failover.webhook_service.fire_failover_event'), \
                patch('failover.probe_target', return_value={
                    'reachable': True, 'status': 200, 'detail': ''}) as probe:
            failover.trigger('llm:primary', 'offline queue test')
            offline_queue_tick(db, background._offline_queue_target_resolver)
        assert [call.args[0] for call in probe.call_args_list] == ['llm:failover']
        assert db.get_episode(SLUG, episode['episode_id'])['status'] == 'pending'

    def test_offline_tick_probes_active_whisper_standby(self, seeded_episode, preserve_setting):
        self._defer('ep-whisper-standby', '2999-01-01T00:00:00Z', service='whisper')
        for key in ('failover_whisper_enabled', 'failover_whisper_backend',
                    'failover_whisper_api_base_url', 'failover_state:whisper',
                    'failover_generation:whisper', 'failover_probe:whisper:failover',
                    'failover_probe_lease:whisper:failover'):
            preserve_setting(key)
        db.set_setting('failover_whisper_enabled', 'true', is_default=False)
        db.set_setting('failover_whisper_backend', 'openai-api', is_default=False)
        db.set_setting('failover_whisper_api_base_url', 'https://standby.example/v1',
                       is_default=False)
        failover.invalidate_cache()
        with patch.object(background, '_resolve_route_snapshot', return_value={}), \
                patch('failover.webhook_service.fire_failover_event'), \
                patch('failover.probe_target', return_value={
                    'reachable': True, 'status': 200, 'detail': ''}) as probe:
            failover.trigger('whisper', 'offline queue test')
            offline_queue_tick(db, background._offline_queue_target_resolver)
        assert [call.args[0] for call in probe.call_args_list] == ['whisper:failover']
        assert db.get_episode(SLUG, 'ep-whisper-standby')['status'] == 'pending'

    def test_passthrough_skips_llm_when_route_snapshot_is_unresolved(
            self, seeded_episode, preserve_setting):
        preserve_setting('secondary_provider_enabled')
        self._defer('ep-passthrough', '2999-01-01T00:00:00Z')
        db.update_podcast(SLUG, passthrough_enabled=True)
        db.set_setting('secondary_provider_enabled', 'true', is_default=False)
        failover.invalidate_cache()
        with patch.object(background, '_resolve_route_snapshot', return_value=None), \
                patch('failover.run_probe_targets') as run_targets:
            resolve = background._offline_queue_target_resolver(db)
            assert resolve('llm', {
                'podcast_slug': SLUG, 'episode_id': 'ep-passthrough'}) == []
        run_targets.assert_not_called()


class TestServiceAlerts:
    def _defer_llm(self):
        db.upsert_episode(SLUG, 'ep-llm', title='ep-llm', status='deferred',
                          original_url='https://example.com/ep-llm.mp3',
                          error_message='Deferred (llm endpoint unreachable)',
                          deferred_at='2999-01-01T00:00:00Z', deferred_service='llm')

    def _defer_whisper(self, episode_id):
        db.upsert_episode(SLUG, episode_id, title=episode_id, status='deferred',
                          original_url=f'https://example.com/{episode_id}.mp3',
                          error_message='Deferred (whisper endpoint unreachable)',
                          deferred_at='2999-01-01T00:00:00Z', deferred_service='whisper')

    def teardown_method(self):
        db.clear_setting('offline_probe_llm_reachable')
        db.clear_setting('offline_probe_whisper_reachable')

    @patch('offline_queue.fire_service_reachable_event')
    def test_probe_false_to_true_fires_reachable(self, mock_fire, seeded_episode):
        self._defer_llm()
        db.set_setting('offline_probe_llm_reachable', 'false', is_default=False)
        with patch('failover.ensure_fresh_probes'), \
                patch('failover.current_probe_state', return_value={'reachable': True}):
            offline_queue_tick(db, _target_resolver())
        mock_fire.assert_called_once_with(service='llm', requeued=1)

    @patch('offline_queue.fire_service_reachable_event')
    def test_first_probe_does_not_fire_reachable(self, mock_fire, seeded_episode):
        self._defer_llm()
        db.clear_setting('offline_probe_llm_reachable')
        with patch('failover.ensure_fresh_probes'), \
                patch('failover.current_probe_state', return_value={'reachable': True}):
            offline_queue_tick(db, _target_resolver())
        mock_fire.assert_not_called()
        assert db.get_episode(SLUG, 'ep-llm')['status'] == 'pending'

    @patch('offline_queue.fire_service_reachable_event')
    def test_each_recovered_service_gets_its_own_count(self, mock_fire, seeded_episode):
        self._defer_llm()
        self._defer_whisper('ep-w1')
        self._defer_whisper('ep-w2')
        db.set_setting('offline_probe_llm_reachable', 'false', is_default=False)
        db.set_setting('offline_probe_whisper_reachable', 'false', is_default=False)
        states = {
            'llm:primary': {'reachable': True},
            'whisper:active': {'reachable': True},
        }
        with patch('failover.ensure_fresh_probes'), \
                patch('failover.current_probe_state', side_effect=states.__getitem__):
            offline_queue_tick(db, _target_resolver())
        assert mock_fire.call_args_list == [
            call(service='llm', requeued=1),
            call(service='whisper', requeued=2),
        ]

    @patch('main_app.processing.fire_service_offline_event')
    def test_deferral_fires_service_offline(self, mock_fire, seeded_episode):
        db.set_setting('offline_queue_enabled', 'true')
        _fail(seeded_episode, ServiceUnavailableError('whisper', 'down'))
        assert mock_fire.call_count == 1
        kwargs = mock_fire.call_args.kwargs
        assert kwargs['service'] == 'whisper'
        assert kwargs['slug'] == SLUG
        assert kwargs['episode_id'] == seeded_episode
        assert db.get_setting('offline_probe_whisper_reachable') == 'false'

    @patch('offline_queue.fire_service_reachable_event')
    @patch('main_app.processing.fire_service_offline_event')
    def test_recovery_within_one_tick_fires_reachable(self, _offline, mock_fire, seeded_episode):
        """The deferral seeds the outage verdict, so the first probe after
        a recovery reads False -> True even with no earlier tick."""
        db.set_setting('offline_queue_enabled', 'true')
        _fail(seeded_episode, ServiceUnavailableError('llm', 'down'))
        with patch('failover.ensure_fresh_probes'), \
                patch('failover.current_probe_state', return_value={'reachable': True}):
            offline_queue_tick(db, _target_resolver())
        mock_fire.assert_called_once_with(service='llm', requeued=1)
        assert db.get_episode(SLUG, seeded_episode)['status'] == 'pending'


class TestLocalTranscriptionUnavailableEpisodeOutcome:
    """Missing local Whisper packages never self-resolve, so the episode must
    fail permanently instead of deferring as a whisper outage (#795)."""

    MISSING = LocalTranscriptionUnavailableError(
        "Local Whisper backend needs faster-whisper and ctranslate2")

    def test_not_transient(self):
        assert is_transient_error(self.MISSING) is False

    def test_not_deferred_when_offline_queue_enabled(self, seeded_episode):
        db.set_setting('offline_queue_enabled', 'true')
        _fail(seeded_episode, self.MISSING)
        episode = db.get_episode(SLUG, seeded_episode)
        assert episode['status'] == 'permanently_failed'
        assert not episode.get('deferred_at')
