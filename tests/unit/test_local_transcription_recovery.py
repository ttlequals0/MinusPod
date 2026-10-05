"""Local recovery requires idle diagnostic decoding of the original configuration."""
import io
import json
import threading
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from tests.app_bootstrap import bootstrap
bootstrap('local_recovery_test_')

import transcriber

local_state_reader = transcriber._latest_local_outcome

CONFIG = {'local_model': 'original-model', 'device': 'cpu', 'compute_type': 'int8',
          'recover_runtime': True}


@pytest.fixture(autouse=True)
def local_state(monkeypatch):
    monkeypatch.setattr(transcriber, '_LOCAL_IMPORT_ERROR', None)
    monkeypatch.setattr(transcriber, '_last_local_transcription_outcome', None)
    monkeypatch.setattr(transcriber, '_latest_local_outcome', lambda device: None)
    monkeypatch.setattr(transcriber, '_local_decode_count', 0)
    monkeypatch.setattr(transcriber, '_local_probe_active', False)
    monkeypatch.setattr(transcriber, '_GPU_ADMISSION_SEMAPHORE', threading.Semaphore(1))
    monkeypatch.setattr(transcriber, 'GPU_TRANSCRIBE_MAX_CONCURRENT', 1)


def test_import_health_does_not_load_a_model_without_recorded_failure(monkeypatch):
    loader = MagicMock()
    monkeypatch.setattr(transcriber.WhisperModelSingleton, 'get_instance', loader)
    result = transcriber.probe_local_transcription({**CONFIG, 'recover_runtime': False})
    assert result['reachable'] is True and 'local_outcome' not in result
    loader.assert_not_called()


def test_original_recovery_decodes_after_standby_success_without_persisting_evidence(temp_db, monkeypatch):
    previous = {'outcome': 'failed', 'model': 'original-model', 'device': 'cpu'}
    temp_db.set_setting(transcriber.LOCAL_OUTCOME_SETTING, json.dumps(previous))
    monkeypatch.setattr(transcriber, '_latest_local_outcome', lambda device: {
        'outcome': 'success', 'model': 'standby-model', 'device': device})
    consumed = []
    def decode(audio, **kwargs):
        assert isinstance(audio, io.BytesIO) and audio.read(4) == b'RIFF'
        def segments():
            consumed.append(True)
            yield object()
        return segments(), None
    model = MagicMock(); model.transcribe.side_effect = decode
    loader = MagicMock(return_value=(model, None))
    monkeypatch.setattr(transcriber.WhisperModelSingleton, 'get_instance', loader)
    result = transcriber.probe_local_transcription(CONFIG)
    assert result['reachable'] is True and consumed == [True]
    assert result['local_outcome']['model'] == 'original-model'
    assert result['local_outcome']['outcome'] == 'success'
    loader.assert_called_once_with(load_config={
        'model': 'original-model', 'device': 'cpu', 'compute_type': 'int8'})
    assert json.loads(temp_db.get_setting(transcriber.LOCAL_OUTCOME_SETTING)) == previous


def test_lazy_decode_failure_does_not_clear_local_failure(monkeypatch):
    monkeypatch.setattr(transcriber, '_latest_local_outcome', lambda device: {
        'outcome': 'failed', 'model': 'original-model', 'device': device})
    def segments():
        raise RuntimeError('decoder failed')
        yield
    model = MagicMock(); model.transcribe.return_value = (segments(), None)
    monkeypatch.setattr(transcriber.WhisperModelSingleton, 'get_instance', lambda **kwargs: (model, None))
    result = transcriber.probe_local_transcription({**CONFIG, 'recover_runtime': False})
    assert result['reachable'] is False and 'local_outcome' not in result
    with transcriber._idle_local_probe() as free:
        assert free


def test_matching_loaded_configuration_is_reused(monkeypatch):
    singleton = transcriber.WhisperModelSingleton
    base, pipeline = object(), object()
    monkeypatch.setattr(singleton, '_base_model', base)
    monkeypatch.setattr(singleton, '_instance', pipeline)
    monkeypatch.setattr(singleton, '_needs_reload', False)
    monkeypatch.setattr(singleton, '_load_config', {
        'model': 'original-model', 'device': 'cpu', 'compute_type': 'int8'})
    constructor = MagicMock()
    monkeypatch.setattr(transcriber, 'WhisperModel', constructor)
    assert singleton.get_instance(load_config=dict(singleton._load_config)) == (base, pipeline)
    constructor.assert_not_called()


def test_cpu_decodes_remain_concurrent_and_idle_unload_skips_them(monkeypatch):
    entered = [threading.Event(), threading.Event()]
    release = threading.Event()
    unload = MagicMock()
    monkeypatch.setattr(transcriber.WhisperModelSingleton, 'is_loaded', lambda: True)
    monkeypatch.setattr(transcriber.WhisperModelSingleton, 'unload_model', unload)
    def decode(index):
        with transcriber._local_decode_activity():
            entered[index].set()
            release.wait(2)
    threads = [threading.Thread(target=decode, args=(i,)) for i in range(2)]
    for thread in threads:
        thread.start()
    try:
        assert all(event.wait(1) for event in entered)
        assert transcriber.probe_local_transcription(CONFIG)['reachable'] is None
        assert not transcriber.unload_whisper_if_idle()
        transcriber.Transcriber.unload_after_repair()
        unload.assert_not_called()
    finally:
        release.set()
        for thread in threads:
            thread.join(2)
    assert all(not thread.is_alive() for thread in threads)


def test_nested_idle_unload_skips_current_decode(monkeypatch):
    unload = MagicMock()
    monkeypatch.setattr(transcriber.WhisperModelSingleton, 'is_loaded', lambda: True)
    monkeypatch.setattr(transcriber.WhisperModelSingleton, 'unload_model', unload)
    with transcriber._local_decode_activity():
        assert not transcriber.unload_whisper_if_idle()
        transcriber.Transcriber.unload_after_repair()
    unload.assert_not_called()


def test_sequential_activity_covers_model_acquisition_and_lazy_segments(monkeypatch):
    observed = []
    def check_busy():
        with transcriber._idle_local_probe() as free:
            observed.append(free)
    def segments():
        check_busy()
        return
        yield
    model = MagicMock(); model.transcribe.return_value = (segments(), None)
    def load():
        check_busy()
        return model, None
    monkeypatch.setattr(transcriber.WhisperModelSingleton, 'get_instance', load)
    monkeypatch.setattr(transcriber, 'active_whisper_settings', lambda: {'backend': 'local', 'language': 'en'})
    monkeypatch.setattr(transcriber, 'resolve_whisper_device', lambda: 'cpu')
    decoder = transcriber.Transcriber()
    monkeypatch.setattr(decoder, 'preprocess_audio', lambda path: None)
    assert decoder._transcribe_sequential('/tmp/example.wav') == []
    assert observed == [False, False]
    with transcriber._idle_local_probe() as free:
        assert free


def test_probe_exclusivity_delays_new_decode_until_released():
    entered = threading.Event()
    def decode():
        with transcriber._local_decode_activity():
            entered.set()
    with transcriber._idle_local_probe() as free:
        assert free
        worker = threading.Thread(target=decode)
        worker.start()
        assert not entered.wait(0.05)
    assert entered.wait(1)
    worker.join(1)
    assert not worker.is_alive()


def test_missing_optional_stack_cannot_report_recovery(monkeypatch):
    monkeypatch.setattr(transcriber, '_LOCAL_IMPORT_ERROR', ImportError('not installed'))
    result = transcriber.probe_local_transcription(CONFIG)
    assert result['reachable'] is False and 'local_outcome' not in result


@pytest.mark.parametrize('entrypoint', ['get_instance', 'get_batched_pipeline'])
@pytest.mark.parametrize('changed', [{'device': 'cpu', 'compute_type': 'float32'},
                                    {'device': 'cuda', 'compute_type': 'int8'}])
def test_normal_load_replaces_stale_captured_device_or_compute(monkeypatch, changed, entrypoint):
    singleton = transcriber.WhisperModelSingleton
    old = {'model': 'original-model', 'device': 'cpu', 'compute_type': 'int8'}
    monkeypatch.setattr(singleton, '_base_model', object())
    monkeypatch.setattr(singleton, '_instance', object())
    monkeypatch.setattr(singleton, '_load_config', old)
    monkeypatch.setattr(singleton, '_needs_reload', False)
    monkeypatch.setattr(singleton, 'get_configured_model', lambda: old['model'])
    monkeypatch.setattr(transcriber, 'resolve_whisper_device', lambda: changed['device'])
    monkeypatch.setattr(transcriber, '_get_whisper_compute_type', lambda: changed['compute_type'])
    def unload():
        singleton._instance = None
        singleton._base_model = None
    unload_mock = MagicMock(side_effect=unload)
    monkeypatch.setattr(singleton, 'unload_model', unload_mock)
    constructor = MagicMock()
    monkeypatch.setattr(transcriber, 'WhisperModel', constructor)
    monkeypatch.setattr(transcriber, 'BatchedInferencePipeline', MagicMock())
    monkeypatch.setattr(transcriber.ctranslate2, 'get_cuda_device_count', lambda: 0)
    getattr(singleton, entrypoint)()
    unload_mock.assert_called_once()
    assert singleton._load_config == {**old, **changed}
    assert constructor.call_args.kwargs['compute_type'] == changed['compute_type']


def test_real_failure_after_diagnostic_success_in_same_second_wins(monkeypatch):
    success = {'outcome': 'success', 'device': 'cpu',
               'observed_at': '2026-01-01T00:00:00.100000Z'}
    monkeypatch.setattr(transcriber, '_latest_local_outcome',
                        local_state_reader)
    monkeypatch.setattr(transcriber, '_read_persisted_local_outcome', lambda: success)
    monkeypatch.setattr(transcriber, 'utc_now', lambda: datetime(
        2026, 1, 1, 0, 0, 0, 200000, tzinfo=timezone.utc))
    monkeypatch.setattr(transcriber.database, 'Database', MagicMock())
    transcriber._record_local_transcription_outcome({'outcome': 'failed', 'device': 'cpu'})
    assert transcriber._latest_local_outcome('cpu')['outcome'] == 'failed'
