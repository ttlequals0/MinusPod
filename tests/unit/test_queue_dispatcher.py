"""Dispatcher keeps up to max_episodes runs in flight; API workers enqueue while the pool is active."""
import threading
import time
from unittest.mock import patch

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
    _queue(3)
    monkeypatch.setattr(background, 'get_pool', lambda: _pool(True, 2))
    running = {'n': 0, 'peak': 0}
    lock = threading.Lock()
    def fake_run(queued):
        with lock:
            running['n'] += 1; running['peak'] = max(running['peak'], running['n'])
        time.sleep(0.2)
        with lock:
            running['n'] -= 1
        db.close_claimed_queue_row(queued['id'], 'completed')
    monkeypatch.setattr(background, '_run_claimed_episode', fake_run)
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
    running = {'n': 0, 'peak': 0}
    lock = threading.Lock()
    def fake_run(queued):
        with lock:
            running['n'] += 1; running['peak'] = max(running['peak'], running['n'])
        time.sleep(0.1)
        with lock:
            running['n'] -= 1
        db.close_claimed_queue_row(queued['id'], 'completed')
    monkeypatch.setattr(background, '_run_claimed_episode', fake_run)
    monkeypatch.setattr(background, 'IDLE_WAIT_SECONDS', 0.05)
    stop = threading.Event()
    monkeypatch.setattr(background, 'shutdown_event', stop)
    t = threading.Thread(target=background.background_queue_processor); t.start()
    deadline = time.time() + 5
    while time.time() < deadline and db.count_pending_queued_episodes():
        time.sleep(0.05)
    stop.set(); t.join(5)
    assert running['peak'] == 1


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
