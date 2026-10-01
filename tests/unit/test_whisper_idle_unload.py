"""Whisper leaves the GPU after the repair passes and whenever the processing queue is idle."""
from unittest.mock import MagicMock, patch

import pytest

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('whisper_idle_unload_test_')
from main_app import background, db
import main_app.processing as processing
import transcriber as transcriber_mod
from processing_queue import ProcessingQueue, set_processing_paused
from transcriber import Transcriber, WhisperModelSingleton, unload_whisper_if_idle
from verification_pass import VerificationPass
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


def test_unload_after_repair_frees_a_reloaded_model(loaded, caplog):
    state, unload = loaded
    with caplog.at_level('INFO', logger='transcriber'):
        Transcriber.unload_after_repair()
    unload.assert_called_once()
    assert state['loaded'] is False
    assert 'Whisper model unloaded after transcript repair' in caplog.text
    Transcriber.unload_after_repair()
    unload.assert_called_once()


def _seg(start, end):
    return {'start': start, 'end': end, 'text': 'x', 'words': []}


@pytest.mark.parametrize('holes', [[], [dict(_seg(20.0, 30.0), novad_hole=True)]])
def test_pass1_repair_unloads_once_with_or_without_holes(holes):
    mock_t = MagicMock()
    with patch.object(processing, 'transcriber', mock_t), \
         patch.object(processing, '_retranscribe_holes_no_vad', return_value=(holes, [])), \
         patch.object(processing, '_retranscribe_tail_no_vad', return_value=[]), \
         patch.object(processing, 'sponsor_service', MagicMock()):
        processing._repair_transcript('show', 'ep1', '/a.mp3', [_seg(0, 10), _seg(40, 50)], None)
    mock_t.unload_after_repair.assert_called_once()


def test_pass1_repair_unloads_even_when_a_pass_raises():
    mock_t = MagicMock()
    with patch.object(processing, 'transcriber', mock_t), \
         patch.object(processing, '_retranscribe_holes_no_vad', side_effect=RuntimeError('boom')):
        with pytest.raises(RuntimeError):
            processing._repair_transcript('show', 'ep1', '/a.mp3', [_seg(0, 10)], None)
    mock_t.unload_after_repair.assert_called_once()


def test_pass2_repair_unloads(monkeypatch):
    import verification_pass
    monkeypatch.setattr(verification_pass, 'get_feed_language_override', lambda db, slug: None)
    mock_t = MagicMock()
    mock_t.transcribe_chunked.return_value = [_seg(0, 10), _seg(40, 50)]
    mock_t.repair_gaps.return_value = ([], [])
    VerificationPass(ad_detector=MagicMock(), transcriber=mock_t,
                     audio_analyzer=MagicMock())._transcribe_verification('/p.mp3', slug='s')
    mock_t.unload_after_repair.assert_called_once()


def test_idle_unload_skips_while_a_transcription_holds_the_gpu(loaded):
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


def _one_pass():
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
    idle = MagicMock(return_value=True)
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
    assert 'Processing queue idle; unloaded the Whisper model' in caplog.text


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
