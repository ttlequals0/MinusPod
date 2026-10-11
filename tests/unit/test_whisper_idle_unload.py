"""The local model unloads only after the queue has stayed idle for LOCAL_MODEL_IDLE_UNLOAD_SECONDS."""
import time
from unittest.mock import MagicMock, patch

import pytest

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('whisper_idle_unload_test_')
from main_app import background, db
import transcriber as transcriber_mod
from processing_queue import ProcessingQueue, set_processing_paused
from transcriber import Transcriber, WhisperModelSingleton, unload_whisper_if_idle
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
    monkeypatch.setattr(transcriber_mod, '_last_local_decode_at', time.monotonic() - 301)


def test_idle_unload_skips_while_a_transcription_holds_the_gpu(loaded, stale):
    _state, unload = loaded
    with patch.object(transcriber_mod, 'GPU_TRANSCRIBE_MAX_CONCURRENT', 1):
        transcriber_mod._GPU_ADMISSION_SEMAPHORE.acquire()
        try:
            assert unload_whisper_if_idle() is False
        finally:
            transcriber_mod._GPU_ADMISSION_SEMAPHORE.release()
        unload.assert_not_called()
        assert unload_whisper_if_idle() is True
    unload.assert_called_once()
    assert unload_whisper_if_idle() is False


def test_recent_decode_keeps_the_model_loaded(loaded, monkeypatch):
    state, unload = loaded
    monkeypatch.setattr(transcriber_mod, '_last_local_decode_at', time.monotonic() - 10)
    assert unload_whisper_if_idle() is False
    unload.assert_not_called()
    assert state['loaded'] is True


def test_decode_301_seconds_ago_unloads(loaded, stale):
    state, unload = loaded
    assert unload_whisper_if_idle() is True
    unload.assert_called_once()
    assert state['loaded'] is False


def test_decode_exit_stamps_the_idle_timer(monkeypatch):
    monkeypatch.setattr(transcriber_mod, '_last_local_decode_at', 0.0)
    with transcriber_mod._local_decode_activity():
        pass
    assert time.monotonic() - transcriber_mod._last_local_decode_at < 5


@pytest.fixture
def feed():
    db.create_podcast(SLUG, 'https://example.com/feed.xml', title='Idle')
    set_processing_paused(False, db)
    ProcessingQueue().clear_all()
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
         patch.object(background, 'unload_whisper_if_idle', idle), \
         patch.object(background, '_run_claimed_episode', claim):
        background.background_queue_processor()
    return idle


def test_idle_queue_unloads_whisper(feed, caplog):
    with caplog.at_level('INFO'):
        idle = _one_pass()
    idle.assert_called_once()
    assert 'Processing queue idle for 300s; unloaded the local transcription model' in caplog.text


def test_idle_branch_unloads_only_after_the_timer(feed, loaded, monkeypatch, caplog):
    state, _unload = loaded
    monkeypatch.setattr(transcriber_mod, '_last_local_decode_at', time.monotonic() - 10)
    with caplog.at_level('INFO'):
        _one_pass(real_unload=True)
    assert state['loaded'] is True
    assert 'unloaded the local transcription model' not in caplog.text
    monkeypatch.setattr(transcriber_mod, '_last_local_decode_at', time.monotonic() - 301)
    with caplog.at_level('INFO'):
        _one_pass(real_unload=True)
    assert state['loaded'] is False
    assert 'unloaded the local transcription model' in caplog.text


def test_no_idle_unload_while_a_run_is_active(feed):
    run_id = ProcessingQueue().acquire(SLUG, 'ep0', limit=99)
    try:
        idle = _one_pass()
    finally:
        ProcessingQueue().release(run_id)
    idle.assert_not_called()


def test_no_idle_unload_on_a_pass_that_claims_a_job(feed):
    db.upsert_episode(SLUG, 'ep1', title='E1', original_url='https://example.com/e.mp3')
    db.upsert_episode_for_processing(SLUG, 'ep1', 'https://example.com/e.mp3', title='E1')
    idle = _one_pass()
    idle.assert_not_called()


def test_chunked_body_keeps_the_model_loaded_after_success(loaded, monkeypatch):
    state, unload = loaded
    t = Transcriber.__new__(Transcriber)
    t.get_audio_duration = MagicMock(return_value=3600.0)
    t.transcribe = MagicMock(return_value=[{'start': 0.0, 'end': 1.0, 'text': 'hi', 'words': []}])
    t.filter_hallucinations = lambda segments: segments

    def extract(*args, **kwargs):
        import tempfile
        handle = tempfile.NamedTemporaryFile(suffix='.wav', delete=False)
        handle.close()
        return handle.name

    with patch('transcriber.calculate_optimal_chunk_duration', return_value=(1800.0, 'test')), \
            patch('transcriber.extract_audio_chunk', side_effect=extract), \
            patch('transcriber._get_whisper_settings', return_value={'backend': 'local'}), \
            patch('transcriber.clear_gpu_memory'):
        result = t.transcribe_chunked('/tmp/input.mp3')
    assert result
    unload.assert_not_called()
    assert state['loaded'] is True
