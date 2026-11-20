"""The local model unloads once no decode ran for the configured idle seconds, even while runs are active."""
import time
import threading
from unittest.mock import MagicMock, patch

import pytest

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('whisper_idle_unload_test_')
from main_app import background, db
import transcriber as transcriber_mod
from processing_queue import ProcessingQueue, set_processing_paused
from transcriber.local import ParakeetModelSingleton, WhisperModelSingleton, unload_whisper_if_idle
from whisper_pool import WhisperPool

SLUG = 'idle-unload-feed'


@pytest.fixture
def loaded(monkeypatch):
    """Pretend a model is resident; unload_model clears it."""
    state = {'loaded': True}
    monkeypatch.setattr(WhisperModelSingleton, 'is_loaded', classmethod(lambda cls: state['loaded']))
    unload = MagicMock(side_effect=lambda: state.update(loaded=False))
    monkeypatch.setattr(WhisperModelSingleton, 'unload_model', unload)
    return state, unload


@pytest.fixture
def stale(monkeypatch):
    """Last local decode finished long enough ago for the timer to have expired."""
    monkeypatch.setattr(transcriber_mod.local, '_last_local_decode_at', time.monotonic() - 301)


def test_idle_unload_skips_while_a_transcription_holds_the_gpu(loaded, stale):
    _state, unload = loaded
    with patch.object(transcriber_mod.local, 'GPU_TRANSCRIBE_MAX_CONCURRENT', 1):
        transcriber_mod.local._GPU_ADMISSION_SEMAPHORE.acquire()
        try:
            assert unload_whisper_if_idle() is False
        finally:
            transcriber_mod.local._GPU_ADMISSION_SEMAPHORE.release()
        unload.assert_not_called()
        assert unload_whisper_if_idle() is True
    unload.assert_called_once()
    assert unload_whisper_if_idle() is False


def test_setting_not_read_when_nothing_is_loaded(monkeypatch):
    monkeypatch.setattr(WhisperModelSingleton, 'is_loaded', classmethod(lambda cls: False))
    monkeypatch.setattr(ParakeetModelSingleton, 'is_loaded', classmethod(lambda cls: False))
    read = MagicMock(return_value=300)
    monkeypatch.setattr(transcriber_mod.local.settings, 'local_model_idle_unload_seconds', read)
    assert unload_whisper_if_idle() is False
    read.assert_not_called()


def test_recent_decode_keeps_the_model_loaded(loaded, monkeypatch):
    state, unload = loaded
    monkeypatch.setattr(transcriber_mod.local, '_last_local_decode_at', time.monotonic() - 10)
    assert unload_whisper_if_idle() is False
    unload.assert_not_called()
    assert state['loaded'] is True


def test_decode_301_seconds_ago_unloads(loaded, stale):
    state, unload = loaded
    assert unload_whisper_if_idle() is True
    unload.assert_called_once()
    assert state['loaded'] is False


def test_decode_exit_stamps_the_idle_timer(monkeypatch):
    monkeypatch.setattr(transcriber_mod.local, '_last_local_decode_at', 0.0)
    with transcriber_mod.local._local_decode_activity():
        pass
    assert time.monotonic() - transcriber_mod.local._last_local_decode_at < 5


@pytest.fixture
def feed():
    db.create_podcast(SLUG, 'https://example.com/feed.xml', title='Idle')
    set_processing_paused(False, db)
    ProcessingQueue().clear_all()
    # A queued row another test left behind would be claimed instead of idling.
    db.get_connection().execute("DELETE FROM auto_process_queue")
    db.get_connection().commit()
    yield
    ProcessingQueue().clear_all()
    db.delete_podcast(SLUG)
    db.get_connection().execute("DELETE FROM auto_process_queue")
    db.get_connection().commit()


def _one_pass(real_unload=False):
    """Run one dispatcher pass and return the idle-unload mock."""
    calls = {'n': 0}

    def claim(queued, running):
        calls['n'] += 1  # stop after this pass
        return 'skipped'

    def fake_wait(timeout=None):
        calls['n'] += 1
        return True

    stop = MagicMock()
    stop.is_set.side_effect = lambda: calls['n'] >= 1
    stop.wait.side_effect = fake_wait
    idle = unload_whisper_if_idle if real_unload else MagicMock(return_value=True)
    pool = WhisperPool(lambda: {'enabled': False, 'backend': 'openai-api',
                                'max_requests': 4, 'max_episodes': 1})
    with patch.object(background, 'shutdown_event', stop), \
         patch.object(background, 'get_pool', lambda: pool), \
         patch.object(transcriber_mod.local, 'unload_whisper_if_idle', idle), \
         patch.object(background, '_run_claimed_episode', claim):
        background.background_queue_processor()
    return idle


def test_idle_check_runs_each_pass(feed):
    _one_pass().assert_called_once()


def test_idle_branch_unloads_only_after_the_timer(feed, loaded, monkeypatch, caplog):
    state, _unload = loaded
    monkeypatch.setattr(transcriber_mod.local, '_last_local_decode_at', time.monotonic() - 10)
    with caplog.at_level('INFO'):
        _one_pass(real_unload=True)
    assert state['loaded'] is True
    assert 'unloaded the local transcription model' not in caplog.text
    monkeypatch.setattr(transcriber_mod.local, '_last_local_decode_at', time.monotonic() - 301)
    with caplog.at_level('INFO'):
        _one_pass(real_unload=True)
    assert state['loaded'] is False
    assert 'unloaded the local transcription model' in caplog.text


def test_idle_check_runs_while_a_run_is_active(feed):
    run_id = ProcessingQueue().acquire(SLUG, 'ep0', limit=99)
    try:
        idle = _one_pass()
    finally:
        ProcessingQueue().release(run_id)
    idle.assert_called_once()


def _set_idle_seconds(value):
    db.set_setting('local_model_idle_unload_seconds', str(value), is_default=False)


@pytest.fixture
def idle_setting():
    yield _set_idle_seconds
    db.reset_setting('local_model_idle_unload_seconds')


def test_unload_fires_after_configured_seconds_with_active_run(feed, loaded, idle_setting, monkeypatch):
    state, _unload = loaded
    idle_setting(60)
    run_id = ProcessingQueue().acquire(SLUG, 'ep0', limit=99)
    try:
        monkeypatch.setattr(transcriber_mod.local, '_last_local_decode_at', time.monotonic() - 30)
        assert unload_whisper_if_idle() is False
        assert state['loaded'] is True
        monkeypatch.setattr(transcriber_mod.local, '_last_local_decode_at', time.monotonic() - 61)
        assert unload_whisper_if_idle() is True
        assert state['loaded'] is False
    finally:
        ProcessingQueue().release(run_id)


def test_no_unload_while_a_decode_is_active(loaded, idle_setting, monkeypatch):
    _state, unload = loaded
    idle_setting(10)
    monkeypatch.setattr(transcriber_mod.local, '_last_local_decode_at', time.monotonic() - 500)
    with transcriber_mod.local._local_decode_activity():
        assert unload_whisper_if_idle() is False
    unload.assert_not_called()


def test_changed_setting_applies_without_restart(loaded, idle_setting, monkeypatch):
    state, _unload = loaded
    monkeypatch.setattr(transcriber_mod.local, '_last_local_decode_at', time.monotonic() - 100)
    assert unload_whisper_if_idle() is False
    idle_setting(90)
    assert unload_whisper_if_idle() is True
    assert state['loaded'] is False


@pytest.mark.parametrize('stored,idle_for,unloads', [(1, 5, False), (99999, 3601, True)])
def test_out_of_range_stored_value_is_clamped(loaded, idle_setting, monkeypatch, stored, idle_for, unloads):
    idle_setting(stored)
    monkeypatch.setattr(transcriber_mod.local, '_last_local_decode_at', time.monotonic() - idle_for)
    assert unload_whisper_if_idle() is unloads


@pytest.mark.parametrize('configured,seconds', [(None, 300), (45, 45)])
def test_unload_log_reports_the_configured_seconds(loaded, idle_setting, monkeypatch, caplog,
                                                   configured, seconds):
    if configured:
        idle_setting(configured)
    monkeypatch.setattr(transcriber_mod.local, '_last_local_decode_at', time.monotonic() - seconds - 1)
    with caplog.at_level('INFO'):
        assert unload_whisper_if_idle() is True
    assert f'Local model idle for {seconds}s; unloaded the local transcription model' in caplog.text


def test_setting_not_read_while_a_decode_is_active(loaded, monkeypatch):
    read = MagicMock(return_value=300)
    monkeypatch.setattr(transcriber_mod.local.settings, 'local_model_idle_unload_seconds', read)
    with transcriber_mod.local._local_decode_activity():
        assert unload_whisper_if_idle() is False
    read.assert_not_called()


def test_single_slot_run_in_flight_is_joined_promptly_with_idle_checks(feed, monkeypatch):
    monkeypatch.setattr(background, 'IDLE_WAIT_SECONDS', 0.02)
    release = threading.Event()
    worker = threading.Thread(target=lambda: release.wait(5))
    claims = iter([{'id': 1}])
    finished = threading.Event()
    alive_at_check = []

    def claim(queued, running):
        worker.start()
        running.add(worker)
        return 'started'

    def idle():
        alive_at_check.append(worker.is_alive())
        if len(alive_at_check) >= 3:
            release.set()
            worker.join()
            finished.set()
        return False

    stop = MagicMock()
    stop.is_set.side_effect = finished.is_set
    pool = WhisperPool(lambda: {'enabled': False, 'backend': 'openai-api',
                                'max_requests': 4, 'max_episodes': 1})
    with patch.object(background, 'shutdown_event', stop), \
         patch.object(background, 'get_pool', lambda: pool), \
         patch.object(background.db, 'claim_next_queued_episode', lambda **kw: next(claims, None)), \
         patch.object(transcriber_mod.local, 'unload_whisper_if_idle', idle), \
         patch.object(background, '_run_claimed_episode', claim):
        background.background_queue_processor()
    assert True in alive_at_check
    stop.wait.assert_not_called()
