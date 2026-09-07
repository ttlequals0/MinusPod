"""Dispatcher keeps up to max_episodes runs in flight; API workers enqueue while the pool is active."""
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('dispatcher_test_')
from main_app import background, db
from main_app.processing import start_background_processing
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


def test_dispatcher_runs_up_to_max_episodes(feed, monkeypatch):
    """A started run's poll/verdict work happens on its own thread
    (_wait_for_claimed_episode), so that is what concurrency is bounded on."""
    _queue(3)
    monkeypatch.setattr(background, 'get_pool', lambda: _pool(True, 2))
    monkeypatch.setattr('main_app.processing.start_background_processing',
                        lambda *a, **k: (True, 'started'))
    running = {'n': 0, 'peak': 0}
    lock = threading.Lock()
    def fake_wait(queue_id, slug, episode_id):
        with lock:
            running['n'] += 1; running['peak'] = max(running['peak'], running['n'])
        time.sleep(0.2)
        with lock:
            running['n'] -= 1
        db.close_claimed_queue_row(queue_id, 'completed')
    monkeypatch.setattr(background, '_wait_for_claimed_episode', fake_wait)
    monkeypatch.setattr(background, 'IDLE_WAIT_SECONDS', 0.05)
    stop = threading.Event()
    monkeypatch.setattr(background, 'shutdown_event', stop)
    t = threading.Thread(target=background.background_queue_processor); t.start()
    deadline = time.time() + 5
    while time.time() < deadline and db.count_pending_queued_episodes():
        time.sleep(0.05)
    stop.set(); t.join(5)
    assert running['peak'] == 2


def test_dispatcher_runs_one_at_a_time_when_inactive(feed, monkeypatch):
    _queue(3)
    monkeypatch.setattr(background, 'get_pool', lambda: _pool(False, 4))
    monkeypatch.setattr('main_app.processing.start_background_processing',
                        lambda *a, **k: (True, 'started'))
    running = {'n': 0, 'peak': 0}
    lock = threading.Lock()
    def fake_wait(queue_id, slug, episode_id):
        with lock:
            running['n'] += 1; running['peak'] = max(running['peak'], running['n'])
        time.sleep(0.1)
        with lock:
            running['n'] -= 1
        db.close_claimed_queue_row(queue_id, 'completed')
    monkeypatch.setattr(background, '_wait_for_claimed_episode', fake_wait)
    monkeypatch.setattr(background, 'IDLE_WAIT_SECONDS', 0.05)
    stop = threading.Event()
    monkeypatch.setattr(background, 'shutdown_event', stop)
    t = threading.Thread(target=background.background_queue_processor); t.start()
    deadline = time.time() + 5
    while time.time() < deadline and db.count_pending_queued_episodes():
        time.sleep(0.05)
    stop.set(); t.join(5)
    assert running['peak'] == 1


def test_dispatcher_picks_up_a_larger_pool_between_passes(feed, monkeypatch):
    """pool.refresh() is called every pass, so a raised max_episodes takes
    effect on the next pass instead of needing a process restart."""
    _queue(5)
    state = {'enabled': True, 'backend': 'openai-api', 'max_requests': 4, 'max_episodes': 1}
    pool = WhisperPool(lambda: dict(state))
    monkeypatch.setattr(background, 'get_pool', lambda: pool)
    monkeypatch.setattr('main_app.processing.start_background_processing',
                        lambda *a, **k: (True, 'started'))
    running = {'n': 0, 'peak': 0}
    lock = threading.Lock()
    calls = {'n': 0}
    def fake_wait(queue_id, slug, episode_id):
        with lock:
            calls['n'] += 1
            if calls['n'] == 3:
                state['max_episodes'] = 2
            running['n'] += 1; running['peak'] = max(running['peak'], running['n'])
        time.sleep(0.2)
        with lock:
            running['n'] -= 1
        db.close_claimed_queue_row(queue_id, 'completed')
    monkeypatch.setattr(background, '_wait_for_claimed_episode', fake_wait)
    monkeypatch.setattr(background, 'IDLE_WAIT_SECONDS', 0.05)
    stop = threading.Event()
    monkeypatch.setattr(background, 'shutdown_event', stop)
    t = threading.Thread(target=background.background_queue_processor); t.start()
    deadline = time.time() + 5
    while time.time() < deadline and db.count_pending_queued_episodes():
        time.sleep(0.05)
    stop.set(); t.join(5)
    assert running['peak'] == 2


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
    monkeypatch.setattr(background, '_is_leader', False)
    started, reason = start_background_processing(SLUG, 'ep-play', 'https://example.com/e.mp3', 'E', 'P', None, None)
    assert (started, reason) == (False, 'queue_only')


def test_start_outside_leader_runs_when_inactive(feed, monkeypatch):
    monkeypatch.setattr('main_app.processing.get_pool', lambda: _pool(False, 2))
    monkeypatch.setattr(background, '_is_leader', False)
    with patch('main_app.processing.threading.Thread') as thread:
        started, reason = start_background_processing(SLUG, 'ep-play', 'https://example.com/e.mp3', 'E', 'P', None, None)
    assert (started, reason) == (True, 'started')
    thread.assert_called_once()
    from processing_queue import ProcessingQueue
    ProcessingQueue().release(SLUG, 'ep-play')
