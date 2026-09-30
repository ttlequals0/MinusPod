"""Transcription OOM stats, GPU admission guard, and no-false-success health.

A GPU-OOM retry must (a) record the outcome (final batch size, retry count,
device, retry-success) so it can ride beside the per-phase LLM breakdown in
processing_stats_json, (b) release GPU memory between attempts and on
exhaustion rather than leaking VRAM into the next episode, (c) preserve the
CUDA cause so the episode retry classifier stops futile retries while
get_local_transcriber_health() reports the transcriber unavailable,
(d) bound concurrent local CUDA transcriptions so two runs cannot
double-allocate VRAM on the same device, and (e) reach /system/status from
any gunicorn worker, which means the outcome lives in shared state and the
reading follows the configured backend.
"""

import json
import tempfile
import threading
import time
import weakref
from unittest.mock import MagicMock, patch

import pytest

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('transcriber_oom_test_')

import database  # noqa: E402
import main_app.processing as processing  # noqa: E402
import transcriber as transcriber_mod  # noqa: E402
from transcriber import Transcriber  # noqa: E402
from main_app.processing import is_transient_error  # noqa: E402
from utils.errors import ModelLoadError  # noqa: E402


def _db():
    return database.Database()


def _fresh():
    _db().set_setting(Transcriber.BATCH_CEILING_SETTING, '')
    _clear_outcome_state()
    return Transcriber()


def _clear_outcome_state(monkeypatch=None):
    """Drop both the in-process mirror and the shared outcome record."""
    _db().set_setting(transcriber_mod.LOCAL_OUTCOME_SETTING, '')
    if monkeypatch is not None:
        monkeypatch.setattr(transcriber_mod, '_last_local_transcription_outcome', None)
    else:
        transcriber_mod._last_local_transcription_outcome = None


def _no_network(*args, **kwargs):
    raise AssertionError('status health reporting must not fire a request')


class _FakePipeline:
    """Raises CUDA OOM for the first `failures` calls, then yields no segments."""

    def __init__(self, failures, error='CUDA out of memory'):
        self.failures = failures
        self.error = error
        self.batch_sizes = []

    def transcribe(self, path, **kwargs):
        self.batch_sizes.append(kwargs['batch_size'])
        if len(self.batch_sizes) <= self.failures:
            raise RuntimeError(self.error)
        info = type('Info', (), {'language': 'en', 'language_probability': 0.99})()
        return iter([]), info


def _patch_common(monkeypatch, pipeline):
    monkeypatch.setenv('WHISPER_DEVICE', 'cuda')
    monkeypatch.setattr(transcriber_mod.WhisperModelSingleton,
                        'get_batched_pipeline', classmethod(lambda cls: pipeline))
    monkeypatch.setattr(transcriber_mod.WhisperModelSingleton,
                        'get_current_model_name', classmethod(lambda cls: 'fake-model'))
    monkeypatch.setattr(transcriber_mod.WhisperModelSingleton,
                        'unload_model', classmethod(lambda cls: None))
    monkeypatch.setattr(Transcriber, 'get_audio_duration', lambda self, p: 30 * 60)
    monkeypatch.setattr(Transcriber, 'preprocess_audio', lambda self, p: None)


def test_oom_reduction_then_success_records_batch_and_retry_stats(monkeypatch):
    """One OOM (16 -> 8) then success: last_transcription_stats carries the
    final (reduced) batch size and the retry it took to get there."""
    pipeline = _FakePipeline(failures=1)
    _patch_common(monkeypatch, pipeline)
    monkeypatch.setattr(Transcriber, 'clear_cuda_cache', lambda self: None)

    t = _fresh()
    result = t.transcribe('/nonexistent/audio.mp3')

    assert result == []
    assert pipeline.batch_sizes == [16, 8]
    stats = t.last_transcription_stats
    assert stats['outcome'] == 'success'
    assert stats['batch_size'] == 8
    assert stats['retry_count'] == 1
    assert stats['retry_succeeded'] is True
    assert stats['device'] == 'cuda'
    assert stats['model'] == 'fake-model'


def test_oom_releases_old_model_before_reload(monkeypatch):
    _patch_common(monkeypatch, MagicMock())
    monkeypatch.setattr(Transcriber, 'clear_cuda_cache', lambda self: None)
    first_ref = None
    loads = 0

    def load_pipeline(cls):
        nonlocal first_ref, loads
        loads += 1
        if loads == 1:
            pipeline = _FakePipeline(failures=1)
            first_ref = weakref.ref(pipeline)
            return pipeline
        assert first_ref() is None
        return _FakePipeline(failures=0)

    monkeypatch.setattr(
        transcriber_mod.WhisperModelSingleton,
        'get_batched_pipeline',
        classmethod(load_pipeline),
    )

    assert _fresh().transcribe('/nonexistent/audio.mp3') == []
    assert loads == 2


def test_exhaustion_records_failed_outcome_and_releases_gpu_memory(monkeypatch):
    """All retries burn OOM: the outcome is recorded as failed (not a
    silent success), and GPU memory is released both between attempts and
    on final cleanup."""
    clear_calls = []
    monkeypatch.setattr(transcriber_mod, 'clear_gpu_memory', lambda: clear_calls.append(1))
    pipeline = _FakePipeline(failures=99)
    _patch_common(monkeypatch, pipeline)

    t = _fresh()
    result = t.transcribe('/nonexistent/audio.mp3')

    assert result is None
    stats = t.last_transcription_stats
    assert stats['outcome'] == 'failed'
    assert stats['retry_count'] == 2
    assert stats['retry_succeeded'] is False
    # clear_gpu_memory ran before each of the 3 attempts, after each of the
    # 2 OOMs, and once more on final cleanup: not a one-shot no-op.
    assert len(clear_calls) >= 3


def test_chunked_cuda_exhaustion_preserves_permanent_error(monkeypatch):
    t = Transcriber.__new__(Transcriber)
    t.get_audio_duration = MagicMock(return_value=3600.0)
    t.transcribe = MagicMock(return_value=None)
    t.filter_hallucinations = lambda segments: segments
    t.last_transcription_stats = {'error': 'CUDA failed with error out of memory'}

    def extract(*args, **kwargs):
        handle = tempfile.NamedTemporaryFile(suffix='.wav', delete=False)
        handle.close()
        return handle.name

    with patch('transcriber.calculate_optimal_chunk_duration',
               return_value=(1800.0, 'test')), \
            patch('transcriber.extract_audio_chunk', side_effect=extract), \
            patch('transcriber._get_whisper_settings',
                  return_value={'backend': 'local'}), \
            patch('transcriber.clear_gpu_memory'), \
            patch.object(transcriber_mod.WhisperModelSingleton,
                         'get_batched_pipeline', return_value=MagicMock()), \
            patch.object(transcriber_mod.WhisperModelSingleton, 'unload_model'):
        with pytest.raises(RuntimeError, match='CUDA.*out of memory') as exc_info:
            t.transcribe_chunked('/tmp/input.mp3')

    assert is_transient_error(exc_info.value) is False


LOAD_OOM = 'CUDA failed with error out of memory'


def _load_model(monkeypatch, compute_type='auto', error=LOAD_OOM):
    """Run get_instance on CUDA with every WhisperModel construction raising error."""
    monkeypatch.setenv('WHISPER_DEVICE', 'cuda')
    singleton = transcriber_mod.WhisperModelSingleton
    for attr in ('_instance', '_base_model', '_current_model_name'):
        monkeypatch.setattr(singleton, attr, None)
    attempts = []

    def fake_model(*args, **kwargs):
        attempts.append(kwargs['compute_type'])
        raise RuntimeError(error)

    with patch('transcriber._get_whisper_settings', return_value={'backend': 'local'}), \
            patch('transcriber._get_whisper_compute_type', return_value=compute_type), \
            patch('transcriber.ctranslate2.get_cuda_device_count', return_value=1), \
            patch('transcriber.WhisperModel', side_effect=fake_model), \
            patch('transcriber.clear_gpu_memory'), \
            patch.object(singleton, 'get_configured_model', return_value='small'):
        singleton.get_instance()
    return attempts


@pytest.mark.parametrize('compute_type', ['auto', 'int8_float16'])
def test_model_load_oom_raises_a_transient_load_error(monkeypatch, compute_type):
    with pytest.raises(ModelLoadError) as exc_info:
        _load_model(monkeypatch, compute_type)
    message = str(exc_info.value).lower()
    assert 'cuda' not in message and 'oom' not in message and 'memory' not in message
    assert LOAD_OOM in str(exc_info.value.__cause__)
    assert is_transient_error(exc_info.value) is True


def test_model_load_failure_other_than_oom_keeps_its_error(monkeypatch):
    with pytest.raises(RuntimeError, match='unsupported') as exc_info:
        _load_model(monkeypatch, error='unsupported on this GPU')
    assert not isinstance(exc_info.value, ModelLoadError)


def test_transcribe_propagates_a_model_load_failure(monkeypatch):
    _patch_common(monkeypatch, None)

    def fail_load(cls):
        raise ModelLoadError('Whisper model could not be loaded')

    monkeypatch.setattr(transcriber_mod.WhisperModelSingleton,
                        'get_batched_pipeline', classmethod(fail_load))
    t = _fresh()
    with pytest.raises(ModelLoadError):
        t.transcribe('/nonexistent/audio.mp3')
    assert t.last_transcription_stats['outcome'] == 'failed'


def _chunked(transcribe):
    t = Transcriber.__new__(Transcriber)
    t.get_audio_duration = MagicMock(return_value=3600.0)
    t.transcribe = transcribe
    t.filter_hallucinations = lambda segments: segments
    t.last_transcription_stats = {}

    def extract(*args, **kwargs):
        handle = tempfile.NamedTemporaryFile(suffix='.wav', delete=False)
        handle.close()
        return handle.name

    with patch('transcriber.calculate_optimal_chunk_duration',
               return_value=(1800.0, 'test')), \
            patch('transcriber.extract_audio_chunk', side_effect=extract), \
            patch('transcriber._get_whisper_settings',
                  return_value={'backend': 'local'}), \
            patch('transcriber.clear_gpu_memory'), \
            patch.object(transcriber_mod.WhisperModelSingleton,
                         'get_batched_pipeline', return_value=MagicMock()), \
            patch.object(transcriber_mod.WhisperModelSingleton, 'unload_model'):
        t.transcribe_chunked('/tmp/input.mp3')


def test_chunked_model_load_failure_is_raised_without_shrinking_chunks():
    transcribe = MagicMock(side_effect=ModelLoadError('Whisper model could not be loaded'))
    with pytest.raises(ModelLoadError):
        _chunked(transcribe)
    assert transcribe.call_count == 1


def _handle_failure(error, retry_count=0):
    episode_data = {'retry_count': retry_count}
    with patch.object(processing, 'db') as db, \
            patch.object(processing, '_record_history_row'), \
            patch.object(processing, '_require_publication_owner'), \
            patch.object(processing, '_publish_status'), \
            patch.object(processing, 'get_episode_token_totals',
                         return_value={'input_tokens': 0, 'output_tokens': 0, 'cost': 0.0}), \
            patch.object(processing, 'is_offline_queue_enabled', return_value=False), \
            patch.object(processing, 'fire_event'):
        db.get_episode.return_value = episode_data
        processing._handle_processing_failure(
            'example-podcast', 'a1b2c3d4e5f6', 'An Episode', 'Example Show',
            episode_data, error, start_time=0.0)
        return db.upsert_episode.call_args.kwargs


def test_model_load_failure_is_retried_not_permanently_failed(caplog):
    with caplog.at_level('INFO'):
        status = _handle_failure(ModelLoadError('Whisper model could not be loaded'))
    assert status['status'] == processing.EpisodeStatus.FAILED.value
    assert status['retry_count'] == 1
    assert 'Whisper model failed to load, will retry' in caplog.text


def test_model_load_failure_respects_the_retry_cap():
    status = _handle_failure(ModelLoadError('Whisper model could not be loaded'),
                             retry_count=processing.MAX_EPISODE_RETRIES - 1)
    assert status['status'] == processing.EpisodeStatus.PERMANENTLY_FAILED.value


def test_chunk_oom_after_reductions_stays_permanent():
    status = _handle_failure(RuntimeError(f'Local CUDA transcription failed: {LOAD_OOM}'))
    assert status['status'] == processing.EpisodeStatus.PERMANENTLY_FAILED.value


def test_admission_guard_serializes_concurrent_cuda_transcriptions(monkeypatch):
    """Two local CUDA transcriptions must never run inside the pipeline at
    the same time: they would double-allocate VRAM on the same device."""
    monkeypatch.setattr(transcriber_mod, '_GPU_ADMISSION_SEMAPHORE', threading.Semaphore(1))
    monkeypatch.setenv('WHISPER_DEVICE', 'cuda')
    monkeypatch.setattr(transcriber_mod.WhisperModelSingleton,
                        'get_current_model_name', classmethod(lambda cls: 'fake-model'))
    monkeypatch.setattr(transcriber_mod.WhisperModelSingleton,
                        'unload_model', classmethod(lambda cls: None))
    monkeypatch.setattr(Transcriber, 'get_audio_duration', lambda self, p: 30 * 60)
    monkeypatch.setattr(Transcriber, 'preprocess_audio', lambda self, p: None)
    monkeypatch.setattr(Transcriber, 'clear_cuda_cache', lambda self: None)

    concurrency = {'current': 0, 'max': 0}
    lock = threading.Lock()

    class _SlowPipeline:
        def transcribe(self, path, **kwargs):
            with lock:
                concurrency['current'] += 1
                concurrency['max'] = max(concurrency['max'], concurrency['current'])
            time.sleep(0.05)
            with lock:
                concurrency['current'] -= 1
            info = type('Info', (), {'language': 'en', 'language_probability': 0.99})()
            return iter([]), info

    monkeypatch.setattr(transcriber_mod.WhisperModelSingleton,
                        'get_batched_pipeline', classmethod(lambda cls: _SlowPipeline()))

    t = _fresh()
    threads = [threading.Thread(target=t.transcribe, args=('/nonexistent/audio.mp3',))
               for _ in range(3)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()

    assert concurrency['max'] == 1


def test_admission_guard_is_a_pass_through_on_cpu(monkeypatch):
    """CPU transcription shares no VRAM, so the guard must not serialize it."""
    monkeypatch.setattr(transcriber_mod, '_GPU_ADMISSION_SEMAPHORE', threading.Semaphore(1))
    pipeline = _FakePipeline(failures=0)
    _patch_common(monkeypatch, pipeline)
    monkeypatch.setenv('WHISPER_DEVICE', 'cpu')
    monkeypatch.setattr(Transcriber, 'clear_cuda_cache', lambda self: None)

    t = _fresh()
    # A held permit (simulating another in-flight CUDA run) must not block a
    # CPU run at all.
    transcriber_mod._GPU_ADMISSION_SEMAPHORE.acquire()
    try:
        result = t.transcribe('/nonexistent/audio.mp3')
    finally:
        transcriber_mod._GPU_ADMISSION_SEMAPHORE.release()

    assert result == []


def test_health_reports_available_with_no_prior_attempt(monkeypatch):
    _clear_outcome_state(monkeypatch)
    health = transcriber_mod.get_local_transcriber_health()
    assert health['available'] is True
    assert health['lastOutcome'] is None


def test_health_reports_available_after_a_successful_run(monkeypatch):
    _clear_outcome_state(monkeypatch)
    monkeypatch.setattr(transcriber_mod, '_last_local_transcription_outcome',
                        {'outcome': 'success', 'batch_size': 8, 'retry_count': 1,
                         'device': 'cpu'})
    health = transcriber_mod.get_local_transcriber_health()
    assert health['available'] is True
    assert health['lastOutcome']['status'] == 'success'


def test_health_reports_unavailable_after_gpu_exhaustion(monkeypatch):
    """The single required guarantee: an OOM exhaustion must not read as a
    healthy transcriber quietly failing episode after episode."""
    _clear_outcome_state(monkeypatch)
    monkeypatch.setattr(transcriber_mod, '_last_local_transcription_outcome',
                        {'outcome': 'failed', 'batch_size': 4, 'retry_count': 2,
                         'retry_succeeded': False, 'device': 'cpu'})
    health = transcriber_mod.get_local_transcriber_health()
    assert health['available'] is False
    assert health['lastOutcome']['status'] == 'failed'


def test_health_ignores_an_outcome_from_another_device(monkeypatch):
    """A record from a device no longer configured says nothing about the
    device now in use."""
    _clear_outcome_state(monkeypatch)
    monkeypatch.setattr(transcriber_mod, '_last_local_transcription_outcome',
                        {'outcome': 'failed', 'device': 'cuda'})
    monkeypatch.setenv('WHISPER_DEVICE', 'cpu')
    health = transcriber_mod.get_local_transcriber_health()
    assert health['available'] is True
    assert health['lastOutcome'] is None


def test_transcribe_records_outcome_into_module_level_health_state(monkeypatch):
    """transcribe() itself keeps get_local_transcriber_health() current, not
    just the per-instance last_transcription_stats."""
    pipeline = _FakePipeline(failures=99)
    _patch_common(monkeypatch, pipeline)

    t = _fresh()
    t.transcribe('/nonexistent/audio.mp3')

    health = transcriber_mod.get_local_transcriber_health()
    assert health['available'] is False
    assert health['lastOutcome']['status'] == 'failed'


def test_recorded_outcome_survives_a_worker_without_the_module_global(monkeypatch):
    """The worker answering /system/status is not necessarily the worker that
    transcribed, so the outcome has to come back from shared state."""
    pipeline = _FakePipeline(failures=99)
    _patch_common(monkeypatch, pipeline)

    t = _fresh()
    t.transcribe('/nonexistent/audio.mp3')

    # Stand in for a second gunicorn worker: same DB, no in-process mirror.
    monkeypatch.setattr(transcriber_mod, '_last_local_transcription_outcome', None)

    health = transcriber_mod.get_local_transcriber_health()
    assert health['available'] is False
    assert health['lastOutcome']['status'] == 'failed'
    assert health['lastOutcome']['device'] == 'cuda'
    assert health['lastOutcome']['backend'] == transcriber_mod.WHISPER_BACKEND_LOCAL
    assert health['lastOutcome']['observedAt']


def test_shared_outcome_wins_over_an_older_in_process_mirror(monkeypatch):
    """Another worker's newer failure must not be masked by this worker's
    older success."""
    _clear_outcome_state(monkeypatch)
    monkeypatch.setenv('WHISPER_DEVICE', 'cpu')
    monkeypatch.setattr(transcriber_mod, '_last_local_transcription_outcome',
                        {'outcome': 'success', 'device': 'cpu',
                         'observed_at': '2026-01-01T00:00:00Z'})
    _db().set_setting(transcriber_mod.LOCAL_OUTCOME_SETTING, json.dumps(
        {'outcome': 'failed', 'device': 'cpu', 'backend': 'local',
         'observed_at': '2026-01-02T00:00:00Z'}))

    health = transcriber_mod.get_local_transcriber_health()
    assert health['available'] is False
    assert health['lastOutcome']['observedAt'] == '2026-01-02T00:00:00Z'


def test_unreadable_shared_outcome_falls_back_to_the_in_process_mirror(monkeypatch):
    _clear_outcome_state(monkeypatch)
    monkeypatch.setenv('WHISPER_DEVICE', 'cpu')
    monkeypatch.setattr(transcriber_mod, '_last_local_transcription_outcome',
                        {'outcome': 'failed', 'device': 'cpu',
                         'observed_at': '2026-01-01T00:00:00Z'})
    _db().set_setting(transcriber_mod.LOCAL_OUTCOME_SETTING, 'banana')

    health = transcriber_mod.get_local_transcriber_health()
    assert health['available'] is False


def test_health_follows_the_configured_backend(monkeypatch):
    """A local OOM exhaustion is not the API backend's health, and must not
    be reported as it once the backend setting points at an API."""
    _clear_outcome_state(monkeypatch)
    monkeypatch.setenv('WHISPER_DEVICE', 'cpu')
    monkeypatch.setattr(transcriber_mod, '_last_local_transcription_outcome',
                        {'outcome': 'failed', 'device': 'cpu',
                         'observed_at': '2026-01-01T00:00:00Z'})
    db = _db()
    try:
        db.set_setting('whisper_backend', transcriber_mod.WHISPER_BACKEND_LOCAL)
        local_health = transcriber_mod.get_transcriber_health()
        assert local_health['backend'] == transcriber_mod.WHISPER_BACKEND_LOCAL
        assert local_health['available'] is False

        db.set_setting('whisper_backend', transcriber_mod.WHISPER_BACKEND_API)
        db.set_setting('whisper_api_base_url', 'http://whisper.invalid/v1')
        api_health = transcriber_mod.get_transcriber_health()
        assert api_health['backend'] == transcriber_mod.WHISPER_BACKEND_API
        assert api_health['probed'] is False
        assert 'lastOutcome' not in api_health
    finally:
        db.set_setting('whisper_backend', transcriber_mod.WHISPER_BACKEND_LOCAL)
        db.set_setting('whisper_api_base_url', '')


def test_api_backend_health_uses_a_cached_probe_not_a_fresh_request(monkeypatch):
    """/system/status is polled, so the API reading comes from the probe
    cache rather than an outbound request per tick."""
    monkeypatch.setattr(transcriber_mod, 'safe_get', _no_network)
    db = _db()
    try:
        db.set_setting('whisper_backend', transcriber_mod.WHISPER_BACKEND_API)
        db.set_setting('whisper_api_base_url', 'http://whisper.invalid/v1')
        transcriber_mod._health_cache.set(
            'http://whisper.invalid/v1',
            {'available': False, 'instances': []})

        health = transcriber_mod.get_transcriber_health()
        assert health['available'] is False
        assert health['probed'] is True
        assert health['instanceCount'] == 0
    finally:
        transcriber_mod._health_cache.delete('http://whisper.invalid/v1')
        db.set_setting('whisper_backend', transcriber_mod.WHISPER_BACKEND_LOCAL)
        db.set_setting('whisper_api_base_url', '')
