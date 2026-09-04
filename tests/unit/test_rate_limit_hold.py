"""Rate-limit queue hold (#696): typed 429-with-reset errors, failure-handler
deferral, queue pause gate, and TTL expiry/release.

Uses the main_app boot pattern from test_offline_queue: bind a temp DATA_DIR
before importing main_app so singletons initialize against it.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('rate_limit_hold_test_')
from llm_client import (
    ProviderRateLimitedError, is_connectivity_error, is_retryable_error,
)
from main_app import db
from main_app.processing import _handle_processing_failure, is_transient_error
from rate_limit_hold import (
    RATE_LIMIT_DEFERRED_SERVICE,
    get_rate_limit_hold_ttl_hours,
    is_queue_paused,
    is_rate_limit_hold_enabled,
    rate_limit_hold_tick,
    should_pause_claims,
)
from tests.unit.provider_error_fakes import FakeResponse, FakeProviderError, call_window


class _FakeRateLimitError(FakeProviderError):
    """A RateLimitError so is_rate_limit_error returns True via string-match."""
    def __init__(self, message="rate limit 429", **kw):
        super().__init__(message, **kw)


def _set_hold_enabled(enabled: bool) -> None:
    """Flip the hold toggle and drop the provider settings cache so the
    5s TTL cache cannot serve a stale flag to the next call."""
    value = 'true' if enabled else 'false'
    db.set_setting('rate_limit_hold_enabled', value)
    # llm_client's cached read constructs its own Database(); the bootstrap
    # pattern can leave that in a different temp dir than main_app.db, so
    # write the toggle through both.
    from database import Database
    Database().set_setting('rate_limit_hold_enabled', value)
    import llm_client
    llm_client.invalidate_provider_cache()


class TestProviderRateLimitedErrorType:
    def test_carries_retry_after(self):
        err = ProviderRateLimitedError('resets soon', retry_after_seconds=600.0)
        assert err.retry_after_seconds == 600.0

    def test_not_retryable(self):
        assert is_retryable_error(ProviderRateLimitedError('x', 600.0)) is False

    def test_not_connectivity(self):
        assert is_connectivity_error(ProviderRateLimitedError('x', 600.0)) is False

    def test_transient_for_processing(self):
        """Transient throttle: with the hold enabled the dedicated branch
        intercepts it; disabled it rides the legacy rate-limited path."""
        assert is_transient_error(ProviderRateLimitedError('x', 600.0)) is True


class TestCallLlmHold:
    """The retry loop must break with a typed error instead of sleeping when
    the hold is enabled and the provider reports a reset."""

    def test_hold_enabled_breaks_with_typed_error(self, monkeypatch):
        from utils import llm_call
        _set_hold_enabled(True)
        calls = {'n': 0}
        err = _FakeRateLimitError(response=FakeResponse(headers={'Retry-After': '600'}))

        class _Client:
            def messages_create(self, **kw):
                calls['n'] += 1
                raise err

        sleeps = []
        monkeypatch.setattr(llm_call.time, 'sleep', lambda s: sleeps.append(s))
        response, last_error = call_window(_Client(), max_retries=5)

        assert response is None
        assert isinstance(last_error, ProviderRateLimitedError)
        assert last_error.retry_after_seconds == 600.0
        assert calls['n'] == 1  # fail fast: no retry attempts
        assert sleeps == []  # worker thread never blocked

    def test_hold_disabled_keeps_sleep_loop(self, monkeypatch):
        from utils import llm_call
        _set_hold_enabled(False)
        calls = {'n': 0}
        err = _FakeRateLimitError(response=FakeResponse(headers={'Retry-After': '600'}))

        class _Client:
            def messages_create(self, **kw):
                calls['n'] += 1
                raise err

        sleeps = []
        monkeypatch.setattr(llm_call.time, 'sleep', lambda s: sleeps.append(s))
        response, last_error = call_window(_Client(), max_retries=1)

        assert response is None
        # Original error surfaces (not the typed hold error); sleeps happened.
        assert not isinstance(last_error, ProviderRateLimitedError)
        assert calls['n'] > 1
        assert len(sleeps) > 0

    def test_hold_enabled_without_retry_after_uses_backoff(self, monkeypatch):
        """No provider-reported reset: no hold, existing backoff path."""
        from utils import llm_call
        _set_hold_enabled(True)
        calls = {'n': 0}

        class _Client:
            def messages_create(self, **kw):
                calls['n'] += 1
                raise _FakeRateLimitError(message='rate limit 429 hit')

        monkeypatch.setattr(llm_call.time, 'sleep', lambda s: None)
        response, last_error = call_window(_Client(), max_retries=1)

        assert response is None
        assert not isinstance(last_error, ProviderRateLimitedError)
        assert calls['n'] > 1

    def test_short_reset_rides_in_process_sleep(self, monkeypatch):
        """Resets at or below the in-process sleep cap never engage the hold:
        a lone throttled window recovers instead of failing its episode."""
        from utils import llm_call
        from rate_limit_hold import MIN_HOLD_RESET_SECONDS
        _set_hold_enabled(True)
        calls = {'n': 0}
        err = _FakeRateLimitError(response=FakeResponse(
            headers={'Retry-After': str(MIN_HOLD_RESET_SECONDS - 1)}))

        class _Client:
            def messages_create(self, **kw):
                calls['n'] += 1
                raise err

        sleeps = []
        monkeypatch.setattr(llm_call.time, 'sleep', lambda s: sleeps.append(s))
        response, last_error = call_window(_Client(), max_retries=1)

        assert response is None
        assert not isinstance(last_error, ProviderRateLimitedError)
        assert calls['n'] > 1
        assert sleeps

    def test_hold_engages_on_final_attempt(self, monkeypatch):
        """The hold check runs before the retries-remaining gate: a held 429
        on the last attempt still breaks with the typed error."""
        from utils import llm_call
        _set_hold_enabled(True)
        calls = {'n': 0}
        filler = FakeProviderError(message='timeout')

        class _Client:
            def messages_create(self, **kw):
                calls['n'] += 1
                if calls['n'] == 1:
                    raise filler
                raise _FakeRateLimitError(
                    response=FakeResponse(headers={'Retry-After': '600'}))

        monkeypatch.setattr(llm_call.time, 'sleep', lambda s: None)
        response, last_error = call_window(_Client(), max_retries=1)

        assert response is None
        assert isinstance(last_error, ProviderRateLimitedError)
        assert calls['n'] == 2

    def test_structural_and_quota_take_precedence_over_hold(self, monkeypatch):
        from utils import llm_call
        from llm_client import StructuralRateLimitError
        _set_hold_enabled(True)
        body = {
            "error": {
                "message": "tokens per minute (TPM): Limit 6000, Used 0, Requested ~7500",
                "type": "tokens",
                "code": "rate_limit_exceeded",
            }
        }
        monkeypatch.setattr(llm_call.time, 'sleep', lambda s: None)
        monkeypatch.setattr(
            'webhook_service.fire_structural_rate_limit_event', lambda *a, **kw: None)

        class _Client:
            def messages_create(self, **kw):
                raise _FakeRateLimitError(
                    body=body, response=FakeResponse(headers={'Retry-After': '60'}))

        response, last_error = call_window(_Client(), max_retries=5)
        assert isinstance(last_error, StructuralRateLimitError)
        assert not isinstance(last_error, ProviderRateLimitedError)


class TestWindowsFailedResponse:
    def test_sets_rate_limited_hold_flags(self):
        from ad_detector import _windows_failed_response
        err = ProviderRateLimitedError('paused', retry_after_seconds=600.0)
        result = _windows_failed_response('pass 1', 3, 3, err, 'test-model')
        assert result['rate_limited_hold'] is True
        assert result['retry_after_seconds'] == 600.0
        assert result['status'] == 'failed'

    def test_absent_for_other_errors(self):
        from ad_detector import _windows_failed_response
        err = _FakeRateLimitError(message='rate limit 429 reached')
        result = _windows_failed_response('pass 1', 3, 3, err, 'test-model')
        assert result.get('rate_limited_hold', False) is False


SLUG = 'rate-limit-hold-feed'


@pytest.fixture
def seeded_episode():
    db.create_podcast(SLUG, 'https://example.com/feed.xml', title='Rate Limit Hold Test')
    db.upsert_episode(SLUG, 'ep-1', title='Episode 1', status='processing',
                      original_url='https://example.com/ep1.mp3', retry_count=1)
    yield 'ep-1'
    db.delete_podcast(SLUG)
    _set_hold_enabled(False)
    db.set_setting('rate_limit_hold_until', '')
    db.clear_setting('rate_limit_hold_since')


def _fail(episode_id, error):
    episode_data = db.get_episode(SLUG, episode_id)
    with patch('main_app.processing.status_service'):
        _handle_processing_failure(SLUG, episode_id, 'Episode 1', 'Rate Limit Hold Test',
                                   episode_data, error, start_time=0.0)


class TestFailureHandlerHold:
    def _hold_error(self, retry_after=600.0):
        return ProviderRateLimitedError('provider rate limit reached', retry_after_seconds=retry_after)

    def test_hold_enabled_defers_with_rate_limit_service(self, seeded_episode):
        _set_hold_enabled(True)
        _fail(seeded_episode, self._hold_error(600.0))
        episode = db.get_episode(SLUG, seeded_episode)
        assert episode['status'] == 'deferred'
        assert episode['deferred_service'] == RATE_LIMIT_DEFERRED_SERVICE
        assert episode['deferred_at']
        assert episode['retry_count'] == 1  # untouched
        assert is_queue_paused(db) is True

    def test_hold_disabled_keeps_rate_limited_failed_path(self, seeded_episode):
        _set_hold_enabled(False)
        _fail(seeded_episode, self._hold_error(600.0))
        episode = db.get_episode(SLUG, seeded_episode)
        assert episode['status'] == 'failed'
        assert episode['retry_count'] == 1  # 429s never burn retry_count (#238)
        assert not episode.get('deferred_at')

    def test_re_hold_keeps_first_deferred_at(self, seeded_episode):
        _set_hold_enabled(True)
        first = '2026-01-01T00:00:00Z'
        db.upsert_episode(SLUG, seeded_episode, deferred_at=first,
                          deferred_service=RATE_LIMIT_DEFERRED_SERVICE)
        _fail(seeded_episode, self._hold_error(1200.0))
        episode = db.get_episode(SLUG, seeded_episode)
        assert episode['status'] == 'deferred'
        assert episode['deferred_at'] == first

    def test_hold_branch_precedes_offline_queue(self, seeded_episode):
        """Both features enabled: a held 429 is a rate-limit hold, not an
        endpoint outage, so the offline queue must not claim it."""
        _set_hold_enabled(True)
        db.set_setting('offline_queue_enabled', 'true')
        _fail(seeded_episode, self._hold_error())
        episode = db.get_episode(SLUG, seeded_episode)
        assert episode['deferred_service'] == RATE_LIMIT_DEFERRED_SERVICE

    def test_untouched_retry_count(self, seeded_episode):
        _set_hold_enabled(True)
        _fail(seeded_episode, self._hold_error())
        episode = db.get_episode(SLUG, seeded_episode)
        assert episode['retry_count'] == 1


class TestExpiryAndRelease:
    def _defer(self, episode_id, deferred_at, service=RATE_LIMIT_DEFERRED_SERVICE):
        db.upsert_episode(SLUG, episode_id, title=episode_id, status='deferred',
                          original_url=f'https://example.com/{episode_id}.mp3',
                          error_message='Paused (LLM rate limit)',
                          deferred_at=deferred_at, deferred_service=service)

    def test_expire_with_hold_service_only_holds(self, seeded_episode):
        self._defer('ep-hold-old', '2020-01-01T00:00:00Z')
        self._defer('ep-hold-young', '2999-01-01T00:00:00Z')
        self._defer('ep-offline', '2020-01-01T00:00:00Z', service='llm')
        expired = db.expire_deferred_episodes(
            48, service='llm_rate_limit', label='Rate-limit hold')
        assert [e['episode_id'] for e in expired] == ['ep-hold-old']
        assert db.get_episode(SLUG, 'ep-hold-old')['status'] == 'permanently_failed'
        assert 'Rate-limit hold TTL expired after 48 hours' in \
            db.get_episode(SLUG, 'ep-hold-old')['error_message']
        assert db.get_episode(SLUG, 'ep-hold-young')['status'] == 'deferred'
        assert db.get_episode(SLUG, 'ep-offline')['status'] == 'deferred'

    def test_offline_expire_skips_rate_limit_holds(self, seeded_episode):
        """The offline tick passes its own exclusion; the DB layer does not
        know which service belongs to which feature."""
        self._defer('ep-held', '2020-01-01T00:00:00Z')
        expired = db.expire_deferred_episodes(
            48, exclude_service=RATE_LIMIT_DEFERRED_SERVICE)
        assert [e['episode_id'] for e in expired] == []
        assert db.get_episode(SLUG, 'ep-held')['status'] == 'deferred'

    def test_offline_expire_covers_whisper_deferrals(self, seeded_episode):
        """The default sweep must keep covering every non-hold service; a
        whisper deferral that outlives its TTL still expires."""
        self._defer('ep-whisper-old', '2020-01-01T00:00:00Z', service='whisper')
        expired = db.expire_deferred_episodes(48)
        assert [e['episode_id'] for e in expired] == ['ep-whisper-old']
        assert db.get_episode(SLUG, 'ep-whisper-old')['status'] == 'permanently_failed'

    def test_requeue_only_touches_the_services_passed(self, seeded_episode):
        self._defer('ep-held', '2999-01-01T00:00:00Z', service='llm_rate_limit')
        self._defer('ep-offline', '2999-01-01T00:00:00Z', service='llm')
        # Callers pass exactly the set they own: the offline tick's probe
        # set never contains the hold service.
        requeued = db.requeue_deferred_episodes({'llm'})
        assert requeued == 1
        assert db.get_episode(SLUG, 'ep-held')['status'] == 'deferred'
        assert db.get_episode(SLUG, 'ep-offline')['status'] == 'pending'

    def test_count_deferred_episodes_by_service(self, seeded_episode):
        self._defer('ep-held', '2999-01-01T00:00:00Z', service='llm_rate_limit')
        self._defer('ep-offline', '2999-01-01T00:00:00Z', service='llm')
        assert db.count_deferred_episodes(service='llm_rate_limit') == 1
        assert db.count_deferred_episodes(service='llm') == 1
        assert db.count_deferred_episodes() == 2

    def test_offline_count_covers_whisper_but_not_holds(self, seeded_episode):
        """The offline queue owns llm and whisper. Counting only 'llm' would
        under-report a Whisper outage as zero waiting."""
        self._defer('ep-held', '2999-01-01T00:00:00Z', service='llm_rate_limit')
        self._defer('ep-llm', '2999-01-01T00:00:00Z', service='llm')
        self._defer('ep-whisper', '2999-01-01T00:00:00Z', service='whisper')
        assert db.count_deferred_episodes(
            exclude_service=RATE_LIMIT_DEFERRED_SERVICE) == 2
        assert {e['episode_id'] for e in db.get_deferred_episodes(
            exclude_service=RATE_LIMIT_DEFERRED_SERVICE)} == {'ep-llm', 'ep-whisper'}

    def test_offline_queue_view_reports_whisper_deferrals(self, seeded_episode):
        from api.settings import _offline_queue_view
        self._defer('ep-held', '2999-01-01T00:00:00Z', service='llm_rate_limit')
        self._defer('ep-whisper', '2999-01-01T00:00:00Z', service='whisper')
        assert _offline_queue_view(db)['deferredCount'] == 1


class TestQueuePause:
    def test_paused_while_hold_until_in_future(self):
        future = (datetime.now(timezone.utc) + timedelta(hours=2)).strftime('%Y-%m-%dT%H:%M:%SZ')
        db.set_setting('rate_limit_hold_until', future)
        assert is_queue_paused(db) is True

    def test_not_paused_when_hold_until_past(self):
        db.set_setting('rate_limit_hold_until', '2020-01-01T00:00:00Z')
        assert is_queue_paused(db) is False

    def test_not_paused_when_unset(self):
        db.set_setting('rate_limit_hold_until', '')
        assert is_queue_paused(db) is False


class TestShouldPauseClaims:
    """The queue processor's whole gate: pause unless a hand-requested row is
    waiting, so a Play never parks behind a provider backoff window."""

    def _pause(self):
        future = (datetime.now(timezone.utc) + timedelta(hours=2)).strftime(
            '%Y-%m-%dT%H:%M:%SZ')
        db.set_setting('rate_limit_hold_until', future)

    def _pending(self, episode_id, user_requested=False, priority=0):
        db.upsert_episode(SLUG, episode_id, title=episode_id,
                          original_url='https://example.com/e.mp3',
                          reprocess_requested_at='2026-01-01T00:00:00Z'
                          if user_requested else None)
        podcast = db.get_podcast_by_slug(SLUG)
        conn = db.get_connection()
        conn.execute(
            """INSERT INTO auto_process_queue
               (podcast_id, episode_id, original_url, title, status, priority)
               VALUES (?, ?, ?, ?, 'pending', ?)""",
            (podcast['id'], episode_id, 'https://example.com/e.mp3', 'E', priority))
        conn.commit()

    def setup_method(self):
        db.set_setting('rate_limit_hold_until', '')
        db.get_connection().execute("DELETE FROM auto_process_queue")
        db.get_connection().commit()

    teardown_method = setup_method

    def test_no_pause_without_a_hold(self, seeded_episode):
        assert should_pause_claims(db) is False

    def test_pauses_while_the_reset_is_ahead(self, seeded_episode):
        self._pause()
        assert should_pause_claims(db) is True

    def test_backlog_row_does_not_lift_the_pause(self, seeded_episode):
        self._pause()
        self._pending('ep-backlog')
        assert should_pause_claims(db) is True

    def test_user_requested_row_lifts_the_pause(self, seeded_episode):
        self._pause()
        self._pending('ep-play', user_requested=True)
        assert should_pause_claims(db) is False

    def test_high_priority_backlog_does_not_lift_the_pause(self, seeded_episode):
        """Intent comes from the episode's own mark, not its priority: a high
        feed plus boosts must not read as a hand-made request."""
        self._pause()
        self._pending('ep-hot', priority=500)
        assert should_pause_claims(db) is True

    def test_zero_manual_boost_does_not_disable_the_hold(self, seeded_episode):
        """A manual boost of 0 is allowed by the UI and once made every row
        look user-requested."""
        db.set_setting('queue_manual_boost', '0')
        self._pause()
        self._pending('ep-backlog')
        assert should_pause_claims(db) is True


class TestTick:
    def _seed_held(self, deferred_at):
        db.upsert_episode(SLUG, 'ep-held', title='held', status='deferred',
                          original_url='https://example.com/ep-held.mp3',
                          error_message='Paused (LLM rate limit)',
                          deferred_at=deferred_at,
                          deferred_service=RATE_LIMIT_DEFERRED_SERVICE)

    def test_tick_releases_after_reset(self, seeded_episode):
        _set_hold_enabled(True)
        recent = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        self._seed_held(recent)
        past = (datetime.now(timezone.utc) - timedelta(seconds=5)).strftime('%Y-%m-%dT%H:%M:%SZ')
        db.set_setting('rate_limit_hold_until', past)
        rate_limit_hold_tick(db)
        episode = db.get_episode(SLUG, 'ep-held')
        assert episode['status'] == 'pending'
        assert is_queue_paused(db) is False

    def test_tick_keeps_paused_until_reset(self, seeded_episode):
        _set_hold_enabled(True)
        recent = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        self._seed_held(recent)
        future = (datetime.now(timezone.utc) + timedelta(hours=2)).strftime('%Y-%m-%dT%H:%M:%SZ')
        db.set_setting('rate_limit_hold_until', future)
        rate_limit_hold_tick(db)
        assert db.get_episode(SLUG, 'ep-held')['status'] == 'deferred'
        assert is_queue_paused(db) is True

    def test_tick_expires_hold_past_ttl(self, seeded_episode):
        _set_hold_enabled(True)
        self._seed_held('2020-01-01T00:00:00Z')
        past = (datetime.now(timezone.utc) - timedelta(seconds=5)).strftime('%Y-%m-%dT%H:%M:%SZ')
        db.set_setting('rate_limit_hold_until', past)
        rate_limit_hold_tick(db)
        old = db.get_episode(SLUG, 'ep-held')
        assert old['status'] == 'permanently_failed'
        assert 'Rate-limit hold TTL expired after 48 hours' in old['error_message']


class TestTtlClamp:
    def test_clamped(self):
        db.set_setting('rate_limit_hold_ttl_hours', '9999')
        assert get_rate_limit_hold_ttl_hours(db) == 720
        db.set_setting('rate_limit_hold_ttl_hours', '0')
        assert get_rate_limit_hold_ttl_hours(db) == 1
        db.set_setting('rate_limit_hold_ttl_hours', 'not-a-number')
        assert get_rate_limit_hold_ttl_hours(db) == 48
        db.set_setting('rate_limit_hold_ttl_hours', '48')

    def test_disabled_default(self):
        assert is_rate_limit_hold_enabled(db) is False


class TestPausedClaims:
    """Mid-pause the processor claims only user-requested rows, so a Play
    runs without the backlog being fired into a throttled provider."""

    def _pending(self, episode_id, user_requested=False, priority=0):
        db.upsert_episode(SLUG, episode_id, title=episode_id,
                          original_url='https://example.com/e.mp3',
                          reprocess_requested_at='2026-01-01T00:00:00Z'
                          if user_requested else None)
        podcast = db.get_podcast_by_slug(SLUG)
        conn = db.get_connection()
        conn.execute(
            """INSERT INTO auto_process_queue
               (podcast_id, episode_id, original_url, title, status, priority)
               VALUES (?, ?, ?, ?, 'pending', ?)""",
            (podcast['id'], episode_id, 'https://example.com/e.mp3', 'E', priority))
        conn.commit()

    def setup_method(self):
        db.get_connection().execute("DELETE FROM auto_process_queue")
        db.get_connection().commit()

    teardown_method = setup_method

    def test_claims_only_the_user_requested_row(self, seeded_episode):
        self._pending('ep-backlog', priority=500)
        self._pending('ep-play', user_requested=True, priority=0)
        claimed = db.claim_next_queued_episode(user_requested_only=True)
        assert claimed['episode_id'] == 'ep-play'
        assert db.claim_next_queued_episode(user_requested_only=True) is None

    def test_unfiltered_claim_still_goes_by_priority(self, seeded_episode):
        self._pending('ep-backlog', priority=500)
        self._pending('ep-play', user_requested=True, priority=0)
        assert db.claim_next_queued_episode()['episode_id'] == 'ep-backlog'


class TestHoldAlerts:
    @patch('main_app.processing.fire_queue_held_event')
    def test_hold_entry_fires_queue_held_once(self, mock_fire, seeded_episode):
        _set_hold_enabled(True)
        _fail(seeded_episode, ProviderRateLimitedError('429', retry_after_seconds=900))
        assert mock_fire.call_count == 1
        kwargs = mock_fire.call_args.kwargs
        assert kwargs['slug'] == SLUG
        assert kwargs['episode_id'] == seeded_episode
        assert kwargs['ttl_hours'] == get_rate_limit_hold_ttl_hours(db)
        assert db.get_setting('rate_limit_hold_since')

    def test_second_hold_keeps_first_hold_since(self, seeded_episode):
        _set_hold_enabled(True)
        _fail(seeded_episode, ProviderRateLimitedError('429', retry_after_seconds=900))
        first_since = db.get_setting('rate_limit_hold_since')
        db.set_setting('rate_limit_hold_since', '2026-01-01T00:00:00Z')
        _fail(seeded_episode, ProviderRateLimitedError('429', retry_after_seconds=1800))
        assert first_since
        assert db.get_setting('rate_limit_hold_since') == '2026-01-01T00:00:00Z'

    @patch('rate_limit_hold.fire_queue_resumed_event')
    def test_tick_fires_resumed_when_hold_clears(self, mock_fire, seeded_episode):
        past = (datetime.now(timezone.utc) - timedelta(minutes=1)).strftime('%Y-%m-%dT%H:%M:%SZ')
        db.set_setting('rate_limit_hold_until', past)
        db.set_setting('rate_limit_hold_since', past)
        db.upsert_episode(SLUG, seeded_episode, status='deferred', deferred_at=past,
                          deferred_service=RATE_LIMIT_DEFERRED_SERVICE)
        rate_limit_hold_tick(db)
        mock_fire.assert_called_once_with(held_since=past, requeued=1)
        assert db.get_setting('rate_limit_hold_since') is None
