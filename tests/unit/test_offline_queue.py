"""Offline queue (#482): connectivity classification, deferral, TTL expiry,
and re-drive.

Uses the main_app boot pattern from test_history_ad_count: bind a temp
DATA_DIR before importing main_app so singletons initialize against it.
"""
import atexit
import os
import shutil
import socket
import sys
import tempfile
from unittest.mock import patch

import pytest
import requests

_test_data_dir = tempfile.mkdtemp(prefix='offline_queue_test_')
os.environ.setdefault('SECRET_KEY', 'test-secret')
os.environ['DATA_DIR'] = _test_data_dir

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

import database
import storage as storage_mod

database.Database._instance = None
database.Database.__init__.__defaults__ = (_test_data_dir,)
database.Database.__new__.__defaults__ = (_test_data_dir,)
storage_mod.Storage.__init__.__defaults__ = (_test_data_dir,)

atexit.register(shutil.rmtree, _test_data_dir, ignore_errors=True)

from llm_client import is_connectivity_error, LimitExceededError, StructuralRateLimitError
from main_app import db
from main_app.processing import _handle_processing_failure, is_transient_error
from offline_queue import offline_queue_tick
from utils.circuit_breaker import CircuitBreakerOpen
from utils.errors import ServiceUnavailableError


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
             patch.object(db, 'record_processing_history') as history, \
             patch('llm_client.check_llm_connectivity', return_value=False):
            offline_queue_tick(db)
        assert history.call_count == 1
        assert webhook.call_count == 1
        kwargs = webhook.call_args.kwargs
        assert kwargs['processing_time'] == 0.0
        assert kwargs['llm_cost'] == 0.0

    def test_tick_no_deferred_makes_no_probe_calls(self, seeded_episode):
        with patch('llm_client.check_llm_connectivity') as llm_probe, \
             patch('transcriber.check_whisper_connectivity') as whisper_probe:
            offline_queue_tick(db)
            llm_probe.assert_not_called()
            whisper_probe.assert_not_called()

    def test_tick_probe_false_requeues_nothing(self, seeded_episode):
        self._defer('ep-llm', '2999-01-01T00:00:00Z', service='llm')
        with patch('llm_client.check_llm_connectivity', return_value=False):
            offline_queue_tick(db)
        assert db.get_episode(SLUG, 'ep-llm')['status'] == 'deferred'

    def test_tick_probe_true_requeues(self, seeded_episode):
        self._defer('ep-llm', '2999-01-01T00:00:00Z', service='llm')
        with patch('llm_client.check_llm_connectivity', return_value=True):
            offline_queue_tick(db)
        assert db.get_episode(SLUG, 'ep-llm')['status'] == 'pending'
