"""Dispatcher keeps up to max_episodes runs in flight; API workers enqueue while the pool is active."""
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('dispatcher_test_')
from main_app import background, db
from main_app.processing import start_background_processing
import whisper_pool
from whisper_pool import WhisperPool

SLUG = 'dispatch-feed'


def _pool(enabled, max_episodes):
    state = {'enabled': enabled, 'backend': 'openai-api',
             'max_requests': 4, 'max_episodes': max_episodes}
    return WhisperPool(lambda: dict(state))


@pytest.fixture
def feed():
    db.create_podcast(SLUG, 'https://example.com/feed.xml', title='Dispatch')
    yield
    db.delete_podcast(SLUG)
    db.get_connection().execute("DELETE FROM auto_process_queue")
    db.get_connection().commit()


def _queue(n):
    for i in range(n):
        db.upsert_episode(SLUG, f'ep{i}', title=f'E{i}', original_url='https://example.com/e.mp3')
        db.upsert_episode_for_processing(SLUG, f'ep{i}', 'https://example.com/e.mp3', title=f'E{i}')


def _run_dispatcher(monkeypatch, pool_factory, sleep=0.2, on_claim=None,
                    idle_wait=0.05, timeout=5):
    """Run background_queue_processor against a fake claimed-episode wait
    that sleeps `sleep` seconds per claim, tracking peak concurrency.

    on_claim(call_number), if given, runs once per claim (1-based) before the
    sleep, under the same lock as the peak update -- used to mutate pool
    state mid-run. Returns the observed peak concurrency.
    """
    monkeypatch.setattr(background, 'get_pool', pool_factory)
    monkeypatch.setattr('main_app.processing.start_background_processing',
                        lambda *a, **k: (True, 'started'))
    running = {'n': 0, 'peak': 0}
    calls = {'n': 0}
    lock = threading.Lock()

    def fake_wait(queue_id, slug, episode_id):
        with lock:
            calls['n'] += 1
            if on_claim:
                on_claim(calls['n'])
            running['n'] += 1; running['peak'] = max(running['peak'], running['n'])
        time.sleep(sleep)
        with lock:
            running['n'] -= 1
        db.close_claimed_queue_row(queue_id, 'completed')

    monkeypatch.setattr(background, '_wait_for_claimed_episode', fake_wait)
    monkeypatch.setattr(background, 'IDLE_WAIT_SECONDS', idle_wait)
    stop = threading.Event()
    monkeypatch.setattr(background, 'shutdown_event', stop)
    t = threading.Thread(target=background.background_queue_processor); t.start()
    deadline = time.time() + timeout
    while time.time() < deadline and db.count_pending_queued_episodes():
        time.sleep(0.05)
    stop.set(); t.join(timeout)
    return running['peak']


def test_dispatcher_runs_up_to_max_episodes(feed, monkeypatch):
    """A started run's poll/verdict work happens on its own thread
    (_wait_for_claimed_episode), so that is what concurrency is bounded on."""
    _queue(3)
    peak = _run_dispatcher(monkeypatch, lambda: _pool(True, 2))
    assert peak == 2


def test_dispatcher_runs_one_at_a_time_when_inactive(feed, monkeypatch):
    _queue(3)
    peak = _run_dispatcher(monkeypatch, lambda: _pool(False, 4), sleep=0.1)
    assert peak == 1


def test_dispatcher_picks_up_a_larger_pool_between_passes(feed, monkeypatch):
    """pool.refresh() is called every pass, so a raised max_episodes takes
    effect on the next pass instead of needing a process restart."""
    _queue(5)
    state = {'enabled': True, 'backend': 'openai-api', 'max_requests': 4, 'max_episodes': 1}
    pool = WhisperPool(lambda: dict(state))

    def bump_max_episodes_on_third_claim(call_number):
        if call_number == 3:
            state['max_episodes'] = 2

    peak = _run_dispatcher(monkeypatch, lambda: pool, on_claim=bump_max_episodes_on_third_claim)
    assert peak == 2


def test_bounced_claim_backs_off_instead_of_spinning(feed):
    """A claim that bounces (queue busy) must not be reclaimed at full speed:
    the dispatcher should wait 30s, then 60s, growing before retrying."""
    _queue(1)
    waits = []
    calls = {'n': 0}

    def fake_wait(timeout=None):
        waits.append(timeout)
        calls['n'] += 1
        return calls['n'] >= 3

    stop = MagicMock()
    stop.is_set.side_effect = lambda: calls['n'] >= 3
    stop.wait.side_effect = fake_wait

    with patch.object(background, 'shutdown_event', stop), \
         patch.object(background, 'get_pool', lambda: _pool(False, 1)), \
         patch('main_app.processing.start_background_processing',
               return_value=(False, 'queue_busy:other:ep')):
        background.background_queue_processor()

    assert waits[:2] == [30, 60]


def test_skipped_claim_does_not_reset_ramped_backoff(feed, monkeypatch):
    """A gate-skipped claim between two bounces must not reset the backoff
    the first bounce already ramped (#parallel-whisper review)."""
    _queue(3)
    results = iter(['bounced', 'skipped', 'bounced'])
    monkeypatch.setattr(background, '_run_claimed_episode', lambda queued, running: next(results))

    waits = []
    calls = {'n': 0}

    def fake_wait(timeout=None):
        waits.append(timeout)
        calls['n'] += 1
        return calls['n'] >= 2

    stop = MagicMock()
    stop.is_set.side_effect = lambda: calls['n'] >= 2
    stop.wait.side_effect = fake_wait

    with patch.object(background, 'shutdown_event', stop), \
         patch.object(background, 'get_pool', lambda: _pool(False, 1)):
        background.background_queue_processor()

    assert waits == [30, 60]


def test_start_outside_leader_enqueues_only_while_active(feed, monkeypatch):
    monkeypatch.setattr('main_app.processing.get_pool', lambda: _pool(True, 2))
    monkeypatch.setattr(whisper_pool, '_is_leader', False)
    started, reason = start_background_processing(SLUG, 'ep-play', 'https://example.com/e.mp3', 'E', 'P', None, None)
    assert (started, reason) == (False, 'queue_only')


def test_start_outside_leader_runs_when_inactive(feed, monkeypatch):
    monkeypatch.setattr('main_app.processing.get_pool', lambda: _pool(False, 2))
    monkeypatch.setattr(whisper_pool, '_is_leader', False)
    with patch('main_app.processing.threading.Thread') as thread:
        started, reason = start_background_processing(SLUG, 'ep-play', 'https://example.com/e.mp3', 'E', 'P', None, None)
    assert (started, reason) == (True, 'started')
    thread.assert_called_once()
    from processing_queue import ProcessingQueue
    ProcessingQueue().release(SLUG, 'ep-play')
