"""Rate-limit queue hold (#696): typed 429-with-reset errors, failure-handler
requeue, queue pause gate, and marker release.

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
from database.queue import compute_queue_priority
from main_app import db
from main_app.processing import (
    _handle_processing_failure, is_transient_error, start_background_processing,
)
from rate_limit_hold import (
    clear_hold_for_provider_change,
    get_active_hold,
    get_any_active_hold,
    hold_message,
    hold_queue_for_provider_limit,
    is_queue_paused,
    is_rate_limit_hold_enabled,
    probe_rate_limit,
    rate_limit_hold_tick,
    record_hold_until,
)
from tests.unit.provider_error_fakes import FakeResponse, FakeProviderError, call_window
from tests.unit.rate_limit_fixtures import PRODUCTION_RESET_BODY


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

    def test_hold_uses_body_reset_over_generic_hour_header(self, monkeypatch):
        """A generic Retry-After: 3600 header next to a body carrying the true,
        farther-out reset must hold for the body's reset, not the header (#696)."""
        from utils import llm_call
        _set_hold_enabled(True)
        err = _FakeRateLimitError(
            body=PRODUCTION_RESET_BODY, response=FakeResponse(headers={'Retry-After': '3600'}))

        class _Client:
            def messages_create(self, **kw):
                raise err

        monkeypatch.setattr(llm_call.time, 'sleep', lambda s: None)
        response, last_error = call_window(_Client(), max_retries=5)

        assert response is None
        assert isinstance(last_error, ProviderRateLimitedError)
        assert last_error.retry_after_seconds == 8129.0

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
    from rate_limit_hold import _provider_hold_suffixes, clear_hold
    for provider in _provider_hold_suffixes(db):
        clear_hold(db, provider)
    db.get_connection().execute("DELETE FROM auto_process_queue")
    db.get_connection().commit()


def _fail(episode_id, error):
    episode_data = db.get_episode(SLUG, episode_id)
    with patch('main_app.processing.status_service'):
        _handle_processing_failure(SLUG, episode_id, 'Episode 1', 'Rate Limit Hold Test',
                                   episode_data, error, start_time=0.0)


class TestFailureHandlerHold:
    def _hold_error(self, retry_after=600.0):
        return ProviderRateLimitedError('provider rate limit reached', retry_after_seconds=retry_after)

    def _queue_row(self, episode_id):
        podcast = db.get_podcast_by_slug(SLUG)
        return db.get_connection().execute(
            "SELECT status, priority FROM auto_process_queue WHERE podcast_id = ? AND episode_id = ?",
            (podcast['id'], episode_id)).fetchone()

    def test_hold_enabled_returns_episode_to_queue(self, seeded_episode):
        _set_hold_enabled(True)
        _fail(seeded_episode, self._hold_error(600.0))
        episode = db.get_episode(SLUG, seeded_episode)
        assert episode['status'] == 'pending'
        assert episode['error_message'] == hold_message(
            db.get_setting('rate_limit_hold_until'), 'provider rate limit reached')
        assert not episode.get('deferred_at')
        assert not episode.get('deferred_service')
        assert episode['retry_count'] == 1  # untouched
        assert self._queue_row(seeded_episode)['status'] == 'pending'
        assert is_queue_paused(db) is True

    def test_hold_releases_the_claimed_row_in_place(self, seeded_episode):
        """The processor's claim marks the row processing; the hold puts it
        back to pending with the priority it was claimed at, so the episode
        resumes from the same queue position."""
        _set_hold_enabled(True)
        queue_id = db.upsert_episode_for_processing(
            SLUG, seeded_episode, 'https://example.com/ep1.mp3', priority=25)
        db.claim_next_queued_episode()
        assert db.get_queue_row_status(queue_id) == 'processing'
        _fail(seeded_episode, self._hold_error())
        row = self._queue_row(seeded_episode)
        assert row['status'] == 'pending'
        assert row['priority'] == 25

    def test_hold_on_a_run_without_a_row_queues_it_at_its_boost(self, seeded_episode):
        """A Play or Reprocess starts outside the queue processor; the hold
        gives it a row with the manual boost so it goes first after the reset."""
        _set_hold_enabled(True)
        db.upsert_episode(SLUG, seeded_episode, reprocess_requested_at='2026-01-01T00:00:00Z')
        db.get_connection().execute("DELETE FROM auto_process_queue")
        db.get_connection().commit()
        _fail(seeded_episode, self._hold_error())
        row = self._queue_row(seeded_episode)
        assert row['status'] == 'pending'
        assert row['priority'] > 0

    def test_hold_on_a_run_without_a_row_marks_user_intent(self, seeded_episode):
        """Only Play and Reprocess start outside the queue, so the new row is a
        manual one and the episode carries the mark the drainer's gate reads."""
        _set_hold_enabled(True)
        db.get_connection().execute("DELETE FROM auto_process_queue")
        db.get_connection().commit()
        _fail(seeded_episode, self._hold_error())
        episode = db.get_episode(SLUG, seeded_episode)
        assert episode['reprocess_requested_at']
        assert episode['reprocess_source'] == 'jit'
        row = self._queue_row(seeded_episode)
        assert row['status'] == 'pending'
        assert row['priority'] == compute_queue_priority(None, None, manual=True)

    def test_hold_keeps_an_existing_reprocess_mark(self, seeded_episode):
        _set_hold_enabled(True)
        db.upsert_episode(SLUG, seeded_episode, reprocess_requested_at='2026-01-01T00:00:00Z',
                          reprocess_source='user')
        db.get_connection().execute("DELETE FROM auto_process_queue")
        db.get_connection().commit()
        _fail(seeded_episode, self._hold_error())
        episode = db.get_episode(SLUG, seeded_episode)
        assert episode['reprocess_requested_at'] == '2026-01-01T00:00:00Z'
        assert episode['reprocess_source'] == 'user'

    def test_hold_with_no_prior_row_reads_the_row_the_run_wrote(self, seeded_episode):
        """A JIT play can start before the episode row exists, so the
        handler gets episode_data=None and must not queue a null URL."""
        _set_hold_enabled(True)
        db.get_connection().execute("DELETE FROM auto_process_queue")
        db.get_connection().commit()
        with patch('main_app.processing.status_service'):
            _handle_processing_failure(SLUG, seeded_episode, 'Episode 1', 'Rate Limit Hold Test',
                                       None, self._hold_error(), start_time=0.0)
        row = self._queue_row(seeded_episode)
        assert row['status'] == 'pending'
        assert db.get_episode(SLUG, seeded_episode)['status'] == 'pending'

    def test_hold_disabled_keeps_rate_limited_failed_path(self, seeded_episode):
        _set_hold_enabled(False)
        _fail(seeded_episode, self._hold_error(600.0))
        episode = db.get_episode(SLUG, seeded_episode)
        assert episode['status'] == 'failed'
        assert episode['retry_count'] == 1  # 429s never burn retry_count (#238)
        assert not episode.get('deferred_at')

    def test_hold_branch_precedes_offline_queue(self, seeded_episode):
        """Both features enabled: a held 429 is a rate-limit hold, not an
        endpoint outage, so the offline queue must not claim it."""
        _set_hold_enabled(True)
        db.set_setting('offline_queue_enabled', 'true')
        _fail(seeded_episode, self._hold_error())
        episode = db.get_episode(SLUG, seeded_episode)
        assert episode['status'] == 'pending'
        assert not episode.get('deferred_service')


class TestStartGate:
    """Every start goes through start_background_processing, so the pause
    blocks a Play or Reprocess as well as the queue processor's claims."""

    def test_start_refused_while_paused(self, seeded_episode):
        future = (datetime.now(timezone.utc) + timedelta(hours=2)).strftime('%Y-%m-%dT%H:%M:%SZ')
        db.set_setting('rate_limit_hold_until', future)
        started, reason = start_background_processing(
            SLUG, 'ep-play', 'https://example.com/e.mp3', 'E', 'P', None, None)
        assert started is False
        assert reason == 'rate_limit_paused'


def _admit_or_refuse(monkeypatch, snapshot, episode_id):
    """Drive start_background_processing far enough to observe the hold
    gate's verdict without starting a real run: acquire() is stubbed to
    fail immediately, so any non-hold refusal proves the gate admitted it.
    """
    from main_app import processing
    from processing_queue import ProcessingQueue
    monkeypatch.setattr(processing, '_resolve_route_snapshot', lambda: snapshot)
    monkeypatch.setattr(ProcessingQueue, 'acquire', lambda self, *a, **kw: None)
    return start_background_processing(
        SLUG, episode_id, 'https://example.com/e.mp3', 'E', 'P', None, None)


def _phase_snapshot(**routes):
    """A route snapshot shaped like _resolve_route_snapshot's real return,
    mapping each phase to an arbitrary provider_key for admission tests."""
    return {phase: {'provider_key': provider, 'configured_model': 'model-x'}
            for phase, provider in routes.items()}


class TestProviderScopedAdmission:
    """A hold on one provider must not pause a run whose phases all use a
    different, healthy provider, and a run needing a held provider must be
    refused before it can spend a call on any other provider it needs.
    """

    def test_hold_on_one_provider_leaves_other_provider_run_admissible(
            self, monkeypatch, seeded_episode):
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%SZ')
        record_hold_until(db, 'provider-a', future)
        snapshot = _phase_snapshot(detection='provider-b', review='provider-b',
                                   verification='provider-b', chapters='provider-b')
        started, reason = _admit_or_refuse(monkeypatch, snapshot, 'ep-admit-b')
        assert started is False
        assert reason == 'queue_busy'  # past the hold gate; acquire() is the stub

    def test_run_needing_two_providers_is_held_while_one_is_held(
            self, monkeypatch, seeded_episode):
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%SZ')
        record_hold_until(db, 'provider-a', future)
        snapshot = _phase_snapshot(detection='provider-a', review='provider-b',
                                   verification='provider-a', chapters='provider-b')
        started, reason = _admit_or_refuse(monkeypatch, snapshot, 'ep-admit-mixed')
        assert started is False
        assert reason == 'rate_limit_paused'  # refused before any provider is called

    def test_no_cross_provider_fallback_when_reviewer_provider_is_held(
            self, monkeypatch, seeded_episode):
        """Detection/verification/chapters are healthy on provider-b; only
        the reviewer's provider-a is held. With the reviewer enabled and in
        use, the run must still be refused wholesale, not started with
        review silently skipped or rerouted."""
        db.set_setting('enable_ad_review', 'true')
        try:
            future = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%SZ')
            record_hold_until(db, 'provider-a', future)
            snapshot = _phase_snapshot(detection='provider-b', review='provider-a',
                                       verification='provider-b', chapters='provider-b')
            started, reason = _admit_or_refuse(monkeypatch, snapshot, 'ep-admit-reviewer')
        finally:
            db.set_setting('enable_ad_review', 'false')
        assert started is False
        assert reason == 'rate_limit_paused'

    def test_disabled_reviewer_provider_does_not_count_toward_admission(
            self, monkeypatch, seeded_episode):
        """enable_ad_review is off by default: a held review_provider must
        not refuse a run that will never actually call the reviewer."""
        assert db.get_setting('enable_ad_review') in (None, '', 'false')
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%SZ')
        record_hold_until(db, 'provider-a', future)
        snapshot = _phase_snapshot(detection='provider-b', review='provider-a',
                                   verification='provider-b', chapters='provider-b')
        started, reason = _admit_or_refuse(monkeypatch, snapshot, 'ep-admit-disabled-review')
        assert started is False
        assert reason == 'queue_busy'  # past the hold gate; acquire() is the stub

    def test_legacy_global_hold_still_blocks_every_provider(self, monkeypatch, seeded_episode):
        """A hold recorded by pre-migration code (the bare, unscoped key)
        still pauses every provider for one release."""
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%SZ')
        db.set_setting('rate_limit_hold_until', future)
        snapshot = _phase_snapshot(detection='provider-b', review='provider-b',
                                   verification='provider-b', chapters='provider-b')
        started, reason = _admit_or_refuse(monkeypatch, snapshot, 'ep-admit-legacy')
        assert started is False
        assert reason == 'rate_limit_paused'


class TestLegacyMigration:
    """Episodes a pre-2.96.2 hold parked as deferred go back to the queue
    they were claimed from, once, at schema-migration time."""

    def _park(self, episode_id, service='llm_rate_limit'):
        db.upsert_episode(SLUG, episode_id, title=episode_id, status='deferred',
                          original_url=f'https://example.com/{episode_id}.mp3',
                          error_message='Paused (LLM rate limit)',
                          deferred_at='2026-01-01T00:00:00Z', deferred_service=service)

    def _run(self):
        conn = db.get_connection()
        conn.execute("DELETE FROM schema_migrations WHERE name = 'requeue_rate_limit_held_episodes'")
        conn.commit()
        db._run_requeue_rate_limit_held_episodes(conn)

    def test_reopens_the_closed_row_with_its_priority(self, seeded_episode):
        self._park('ep-held')
        queue_id = db.upsert_episode_for_processing(
            SLUG, 'ep-held', 'https://example.com/ep-held.mp3', priority=40)
        db.claim_next_queued_episode()
        db.close_claimed_queue_row(queue_id, 'completed')
        self._run()
        assert db.get_episode(SLUG, 'ep-held')['status'] == 'pending'
        assert db.get_episode(SLUG, 'ep-held')['deferred_service'] is None
        row = db.get_connection().execute(
            "SELECT status, priority FROM auto_process_queue WHERE id = ?", (queue_id,)).fetchone()
        assert row['status'] == 'pending'
        assert row['priority'] == 40

    def test_inserts_a_row_when_none_exists_and_leaves_offline_rows(self, seeded_episode):
        self._park('ep-held')
        self._park('ep-offline', service='llm')
        db.get_connection().execute("DELETE FROM auto_process_queue")
        db.get_connection().commit()
        self._run()
        assert db.get_episode(SLUG, 'ep-held')['status'] == 'pending'
        assert db.get_episode(SLUG, 'ep-offline')['status'] == 'deferred'
        podcast = db.get_podcast_by_slug(SLUG)
        rows = db.get_connection().execute(
            "SELECT episode_id, status FROM auto_process_queue WHERE podcast_id = ?",
            (podcast['id'],)).fetchall()
        assert [(r['episode_id'], r['status']) for r in rows] == [('ep-held', 'pending')]

    def test_runs_once(self, seeded_episode):
        self._run()
        self._park('ep-held')
        db._run_requeue_rate_limit_held_episodes(db.get_connection())
        assert db.get_episode(SLUG, 'ep-held')['status'] == 'deferred'


class TestPauseGate:
    """The queue processor's gate: while the reset is ahead nothing is
    claimed, a hand-requested row included."""

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

    def test_user_requested_row_does_not_lift_the_pause(self, seeded_episode):
        self._pause()
        self._pending('ep-play', user_requested=True)
        assert is_queue_paused(db) is True

    def test_claim_goes_by_priority(self, seeded_episode):
        self._pending('ep-backlog', priority=500)
        self._pending('ep-play', user_requested=True, priority=0)
        assert db.claim_next_queued_episode()['episode_id'] == 'ep-backlog'


class TestTick:
    def test_tick_clears_marker_after_reset(self, seeded_episode):
        past = (datetime.now(timezone.utc) - timedelta(seconds=5)).strftime('%Y-%m-%dT%H:%M:%SZ')
        db.set_setting('rate_limit_hold_until', past)
        rate_limit_hold_tick(db)
        assert db.get_setting('rate_limit_hold_until') is None
        assert is_queue_paused(db) is False

    def test_tick_keeps_a_marker_restamped_under_it(self, seeded_episode):
        """A 429 that lands between the tick's read and its clear owns a newer
        marker; clearing that one would resume straight into the limit."""
        past = (datetime.now(timezone.utc) - timedelta(seconds=5)).strftime('%Y-%m-%dT%H:%M:%SZ')
        future = (datetime.now(timezone.utc) + timedelta(hours=2)).strftime('%Y-%m-%dT%H:%M:%SZ')
        db.set_setting('rate_limit_hold_until', future)
        db.set_setting('rate_limit_hold_since', '2026-01-01T00:00:00Z')
        with patch('rate_limit_hold.get_hold_until', return_value=past):
            rate_limit_hold_tick(db)
        assert db.get_setting('rate_limit_hold_until') == future
        assert db.get_setting('rate_limit_hold_since') == '2026-01-01T00:00:00Z'
        assert is_queue_paused(db) is True

    def test_clear_setting_if_equal_only_matches_the_stored_value(self, seeded_episode):
        db.set_setting('rate_limit_hold_until', 'A')
        assert db.clear_setting_if_equal('rate_limit_hold_until', 'B') is False
        assert db.get_setting('rate_limit_hold_until') == 'A'
        assert db.clear_setting_if_equal('rate_limit_hold_until', 'A') is True
        assert db.get_setting('rate_limit_hold_until') is None

    def test_tick_keeps_marker_until_reset(self, seeded_episode):
        future = (datetime.now(timezone.utc) + timedelta(hours=2)).strftime('%Y-%m-%dT%H:%M:%SZ')
        db.set_setting('rate_limit_hold_until', future)
        rate_limit_hold_tick(db)
        assert is_queue_paused(db) is True


class TestProviderScopedPauseProbeAndTick:
    """A single-provider install's real 429s land on a provider-scoped key
    (record_hold_until(db, 'anthropic', ...)), not the legacy one. The
    dispatcher's blanket pause, the early-recovery probe, and the tick's
    cleanup must all still react to that hold exactly as they do for a
    legacy one. TestPauseGate/TestTick above only exercise
    record_hold_until(db, None, ...) and would miss this regression.
    """

    def test_any_active_hold_reports_a_provider_scoped_pause(self, seeded_episode):
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%SZ')
        record_hold_until(db, 'anthropic', future)
        hold_until, hold_since = get_any_active_hold(db)
        assert hold_until == future
        assert hold_since
        assert is_queue_paused(db) is False  # legacy key untouched

    @patch('rate_limit_hold.fire_queue_resumed_event')
    def test_probe_clears_the_effective_providers_own_key(self, mock_fire, seeded_episode):
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%SZ')
        record_hold_until(db, 'anthropic', future)
        db.set_setting('llm_usage_url', 'https://example-provider.test/v1/usage')
        db.clear_setting('rate_limit_probe_at')
        db.set_setting('rate_limit_probe_minutes', '5')
        with patch('rate_limit_hold.get_effective_provider', return_value='anthropic'), \
             patch('rate_limit_hold.read_usage_status', return_value={'blocked': False}):
            assert probe_rate_limit(db) is True
        assert is_queue_paused(db, 'anthropic') is False
        mock_fire.assert_called_once()
        db.clear_setting('llm_usage_url')

    @patch('rate_limit_hold.fire_queue_resumed_event')
    def test_tick_clears_an_expired_provider_scoped_marker(self, mock_fire, seeded_episode):
        past = (datetime.now(timezone.utc) - timedelta(seconds=5)).strftime('%Y-%m-%dT%H:%M:%SZ')
        record_hold_until(db, 'anthropic', past)
        rate_limit_hold_tick(db)
        assert is_queue_paused(db, 'anthropic') is False
        mock_fire.assert_called_once()


class TestToggleDefault:
    def test_disabled_default(self):
        assert is_rate_limit_hold_enabled(db) is False


class TestHoldHelperDisabled:
    @patch('rate_limit_hold.fire_queue_held_event')
    def test_toggle_off_records_no_hold(self, mock_fire, seeded_episode):
        _set_hold_enabled(False)
        held = hold_queue_for_provider_limit(
            db, ProviderRateLimitedError('429', retry_after_seconds=900),
            slug=SLUG, episode_id=seeded_episode, podcast_name='Rate Limit Hold Test')
        assert held is None
        assert not db.get_setting('rate_limit_hold_until')
        assert not db.get_setting('rate_limit_hold_since')
        mock_fire.assert_not_called()


class TestActiveHold:
    def test_masks_a_marker_past_its_reset(self, seeded_episode):
        db.set_setting('rate_limit_hold_until', '2020-01-01T00:00:00Z')
        db.set_setting('rate_limit_hold_since', '2020-01-01T00:00:00Z')
        assert get_active_hold(db) == (None, None)

    def test_reports_an_active_marker(self, seeded_episode):
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%SZ')
        db.set_setting('rate_limit_hold_until', future)
        db.set_setting('rate_limit_hold_since', '2026-01-01T00:00:00Z')
        assert get_active_hold(db) == (future, '2026-01-01T00:00:00Z')


class TestHoldAlerts:
    @patch('rate_limit_hold.fire_queue_held_event')
    def test_hold_entry_fires_queue_held_once(self, mock_fire, seeded_episode):
        _set_hold_enabled(True)
        _fail(seeded_episode, ProviderRateLimitedError('429', retry_after_seconds=900))
        assert mock_fire.call_count == 1
        kwargs = mock_fire.call_args.kwargs
        assert kwargs['slug'] == SLUG
        assert kwargs['episode_id'] == seeded_episode
        assert kwargs['hold_until'] == db.get_setting('rate_limit_hold_until')
        assert db.get_setting('rate_limit_hold_since')

    @patch('rate_limit_hold.fire_queue_held_event')
    def test_shorter_reset_under_active_hold_does_not_fire(self, mock_fire, seeded_episode):
        _set_hold_enabled(True)
        _fail(seeded_episode, ProviderRateLimitedError('429', retry_after_seconds=1800))
        first_until = db.get_setting('rate_limit_hold_until')
        _fail(seeded_episode, ProviderRateLimitedError('429', retry_after_seconds=900))
        assert mock_fire.call_count == 1
        assert db.get_setting('rate_limit_hold_until') == first_until

    @patch('rate_limit_hold.fire_queue_held_event')
    def test_longer_reset_extends_hold_without_firing_again(self, mock_fire, seeded_episode):
        """One alert per pause: a user-requested episode claimed during the
        hold 429s too and pushes the reset out, which is not a new pause."""
        _set_hold_enabled(True)
        _fail(seeded_episode, ProviderRateLimitedError('429', retry_after_seconds=900))
        first_until = db.get_setting('rate_limit_hold_until')
        _fail(seeded_episode, ProviderRateLimitedError('429', retry_after_seconds=1800))
        assert mock_fire.call_count == 1
        assert db.get_setting('rate_limit_hold_until') > first_until

    @patch('rate_limit_hold.fire_queue_held_event')
    def test_hold_after_the_previous_one_lapsed_fires_again(self, mock_fire, seeded_episode):
        _set_hold_enabled(True)
        _fail(seeded_episode, ProviderRateLimitedError('429', retry_after_seconds=900))
        db.set_setting('rate_limit_hold_until', '2026-01-01T00:00:00Z')
        _fail(seeded_episode, ProviderRateLimitedError('429', retry_after_seconds=900))
        assert mock_fire.call_count == 2

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
        rate_limit_hold_tick(db)
        mock_fire.assert_called_once_with(held_since=past)
        assert db.get_setting('rate_limit_hold_since') is None

    @patch('rate_limit_hold.fire_queue_resumed_event')
    def test_tick_stays_quiet_while_paused(self, mock_fire, seeded_episode):
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%SZ')
        db.set_setting('rate_limit_hold_until', future)
        rate_limit_hold_tick(db)
        mock_fire.assert_not_called()


class TestProviderChangeClear:
    @patch('rate_limit_hold.fire_queue_resumed_event')
    def test_lifts_an_active_hold_once(self, mock_fire, seeded_episode):
        future = (datetime.now(timezone.utc) + timedelta(hours=2)).strftime('%Y-%m-%dT%H:%M:%SZ')
        db.set_setting('rate_limit_hold_until', future)
        db.set_setting('rate_limit_hold_since', '2026-01-01T00:00:00Z')
        assert clear_hold_for_provider_change(db, 'provider changed') is True
        assert is_queue_paused(db) is False
        assert db.get_setting('rate_limit_hold_since') is None
        mock_fire.assert_called_once_with(held_since='2026-01-01T00:00:00Z')

    @patch('rate_limit_hold.fire_queue_resumed_event')
    def test_no_hold_is_a_no_op(self, mock_fire, seeded_episode):
        assert clear_hold_for_provider_change(db, 'provider changed') is False
        mock_fire.assert_not_called()

    @patch('rate_limit_hold.fire_queue_resumed_event')
    def test_lapsed_marker_is_not_a_hold(self, mock_fire, seeded_episode):
        db.set_setting('rate_limit_hold_until', '2020-01-01T00:00:00Z')
        assert clear_hold_for_provider_change(db, 'provider changed') is False
        mock_fire.assert_not_called()


class TestProviderScopedHoldStorage:
    """record_hold_until/get_active_hold/is_queue_paused directly, with an
    explicit provider_key: a hold on one provider must not be visible when
    a different provider is queried, and the legacy unscoped marker must
    still be honored as a fallback for either.
    """

    def test_record_writes_only_that_providers_key(self, seeded_episode):
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%SZ')
        record_hold_until(db, 'provider-a', future)
        assert is_queue_paused(db, 'provider-a') is True
        assert is_queue_paused(db, 'provider-b') is False
        assert is_queue_paused(db) is False  # legacy key untouched

    def test_get_active_hold_is_provider_specific(self, seeded_episode):
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%SZ')
        record_hold_until(db, 'provider-a', future)
        hold_until, hold_since = get_active_hold(db, 'provider-a')
        assert hold_until == future
        assert hold_since  # a fresh pause stamps its start
        assert get_active_hold(db, 'provider-b') == (None, None)

    def test_legacy_marker_is_a_fallback_for_any_provider(self, seeded_episode):
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%SZ')
        db.set_setting('rate_limit_hold_until', future)  # pre-migration write
        assert is_queue_paused(db, 'provider-a') is True
        assert is_queue_paused(db, 'provider-b') is True
        assert get_active_hold(db, 'provider-a')[0] == future

    def test_clear_for_provider_change_leaves_other_providers_held(self, seeded_episode):
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%SZ')
        record_hold_until(db, 'provider-a', future)
        record_hold_until(db, 'provider-b', future)
        assert clear_hold_for_provider_change(
            db, 'provider-a credentials changed', provider_key='provider-a') is True
        assert is_queue_paused(db, 'provider-a') is False
        assert is_queue_paused(db, 'provider-b') is True


class TestCredentialSlotScopedHolds:
    """A provider can have two independent accounts (primary/secondary); a
    429 on one must not pause the other same-type account.
    """

    def test_secondary_hold_does_not_pause_primary(self, seeded_episode):
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%SZ')
        record_hold_until(db, 'anthropic', future, credential_slot='secondary')
        assert is_queue_paused(db, 'anthropic', 'secondary') is True
        assert is_queue_paused(db, 'anthropic', 'primary') is False
        assert is_queue_paused(db, 'anthropic') is False  # default slot is primary

    def test_primary_hold_does_not_pause_secondary(self, seeded_episode):
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%SZ')
        record_hold_until(db, 'anthropic', future, credential_slot='primary')
        assert is_queue_paused(db, 'anthropic', 'primary') is True
        assert is_queue_paused(db, 'anthropic', 'secondary') is False

    def test_admission_admits_primary_only_run_while_secondary_is_held(
            self, monkeypatch, seeded_episode):
        """A hold on (anthropic, secondary) must not block a run whose
        phases all resolve to (anthropic, primary)."""
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%SZ')
        record_hold_until(db, 'anthropic', future, credential_slot='secondary')
        snapshot = {
            phase: {'provider_key': 'anthropic', 'configured_model': 'model-x',
                    'credential_slot': 'primary'}
            for phase in ('detection', 'review', 'verification', 'chapters')
        }
        started, reason = _admit_or_refuse(monkeypatch, snapshot, 'ep-admit-primary-only')
        assert started is False
        assert reason == 'queue_busy'  # past the hold gate; acquire() is the stub

    def test_admission_refuses_run_needing_the_held_secondary_slot(
            self, monkeypatch, seeded_episode):
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%SZ')
        record_hold_until(db, 'anthropic', future, credential_slot='secondary')
        snapshot = {
            phase: {'provider_key': 'anthropic', 'configured_model': 'model-x',
                    'credential_slot': 'secondary'}
            for phase in ('detection', 'review', 'verification', 'chapters')
        }
        started, reason = _admit_or_refuse(monkeypatch, snapshot, 'ep-admit-secondary-held')
        assert started is False
        assert reason == 'rate_limit_paused'

    def test_get_active_hold_is_slot_specific(self, seeded_episode):
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%SZ')
        record_hold_until(db, 'anthropic', future, credential_slot='secondary')
        hold_until, hold_since = get_active_hold(db, 'anthropic', 'secondary')
        assert hold_until == future
        assert hold_since
        assert get_active_hold(db, 'anthropic', 'primary') == (None, None)

    def test_legacy_type_only_key_falls_back_for_primary_not_secondary(self, seeded_episode):
        """A hold recorded before slot-scoping (a bare per-provider marker)
        still blocks primary for one release, but must not bleed onto a
        secondary account that could not have caused it (secondary did not
        exist yet)."""
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%SZ')
        record_hold_until(db, 'anthropic', future)  # pre-slot-scoping call site
        assert is_queue_paused(db, 'anthropic', 'primary') is True
        assert is_queue_paused(db, 'anthropic', 'secondary') is False

    def test_global_legacy_key_falls_back_for_primary_not_secondary(self, seeded_episode):
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%SZ')
        db.set_setting('rate_limit_hold_until', future)  # fully-unscoped pre-migration write
        assert is_queue_paused(db, 'anthropic', 'primary') is True
        assert is_queue_paused(db, 'anthropic', 'secondary') is False

    def test_clear_for_secondary_change_does_not_lift_primary_hold(self, seeded_episode):
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%SZ')
        record_hold_until(db, 'anthropic', future, credential_slot='primary')
        record_hold_until(db, 'anthropic', future, credential_slot='secondary')
        assert clear_hold_for_provider_change(
            db, 'secondary provider settings changed',
            provider_key='anthropic', credential_slot='secondary') is True
        assert is_queue_paused(db, 'anthropic', 'secondary') is False
        assert is_queue_paused(db, 'anthropic', 'primary') is True

    def test_clear_for_secondary_change_does_not_falsely_report_lifting_a_primary_hold(
            self, seeded_episode):
        """Only a legacy (pre-slot) hold is active; a secondary credential
        change must not report success while leaving that hold (which may
        genuinely belong to primary) untouched."""
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%SZ')
        record_hold_until(db, 'anthropic', future)  # pre-slot-scoping marker
        assert clear_hold_for_provider_change(
            db, 'secondary provider settings changed',
            provider_key='anthropic', credential_slot='secondary') is False
        assert is_queue_paused(db, 'anthropic', 'primary') is True

    def test_status_hold_block_reflects_a_slot_scoped_hold(self, seeded_episode):
        from api.status import _build_hold_block
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%SZ')
        record_hold_until(db, 'anthropic', future, credential_slot='secondary')
        block = _build_hold_block(db)
        assert block['queuePaused'] is True
        assert block['holdUntil'] == future
