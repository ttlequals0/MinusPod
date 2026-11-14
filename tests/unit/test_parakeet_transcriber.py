"""Parakeet transcriber: pure helpers and decode with a stubbed model (#834)."""
import errno
import fcntl
import json
import logging
import os
import shutil
import socket
import sys
import threading
import types
import wave
from pathlib import Path

import httpx
import huggingface_hub
import numpy as np
import pytest
from huggingface_hub.errors import HfHubHTTPError, OfflineModeIsEnabled

import parakeet_transcriber as pt
from utils.errors import ModelLoadError

M = ' '  # onnx-asr replaces the SentencePiece marker with a space
MARK = '\u2581'


def test_plan_windows_two_windows_for_31s():
    plan = pt.plan_windows(31.0, 30.0, 2.0)
    assert plan == [(0.0, 30.0, 0.0, 29.0), (28.0, 31.0, 29.0, 31.0)]


def test_plan_windows_single_window_for_10s():
    assert pt.plan_windows(10.0, 30.0, 2.0) == [(0.0, 10.0, 0.0, 10.0)]


def test_plan_windows_cores_tile_to_total():
    plan = pt.plan_windows(100.0, 30.0, 2.0)
    assert plan[-1][3] == 100.0
    for a, b in zip(plan, plan[1:], strict=False):
        assert a[3] == b[2]


def test_words_from_tokens_markers_end_rule_and_offset():
    words = pt.words_from_tokens([M + 'Hel', 'lo', M + 'there', '.'], [0.0, 0.08, 0.5, 0.58], 10.0)
    assert [w['word'] for w in words] == ['Hello', 'there.']
    assert words[0] == {'word': 'Hello', 'start': 10.0, 'end': 10.5}
    assert words[1]['start'] == 10.5
    assert words[1]['end'] == pytest.approx(10.5 + 0.08 + 0.08)


def test_words_from_tokens_end_capped():
    words = pt.words_from_tokens([M + 'a', M + 'b'], [0.0, 5.0])
    assert words[0]['end'] == pt.PARAKEET_WORD_MAX_S


def test_drop_non_latin_words():
    words = [{'word': w} for w in ('hello', '\u043f\u0440\u0438\u0432\u0435\u0442', 'co-op', '2024', '$5')]
    kept, dropped = pt.drop_non_latin_words(words)
    assert dropped == 1
    assert [w['word'] for w in kept] == ['hello', 'co-op', '2024', '$5']


def _w(text, start, end):
    return {'word': text, 'start': start, 'end': end}


def test_group_segments_splits_on_punctuation():
    segs = pt.group_segments([_w('Hi.', 0, 0.5), _w('Bye', 0.6, 1.0)])
    assert [s['text'] for s in segs] == ['Hi.', 'Bye']
    assert segs[1]['start'] == 0.6 and segs[1]['end'] == 1.0


def test_group_segments_splits_on_pause():
    segs = pt.group_segments([_w('a', 0, 0.5), _w('b', 2.0, 2.5)])
    assert len(segs) == 2


def test_group_segments_caps_length():
    words = [_w('w', i * 0.5, i * 0.5 + 0.4) for i in range(80)]
    segs = pt.group_segments(words)
    assert len(segs) > 1
    assert all(s['end'] - s['start'] <= pt.PARAKEET_SEGMENT_MAX_S for s in segs)
    assert sum(len(s['words']) for s in segs) == 80


def _write_wav(path, seconds, rate=16000):
    data = (np.sin(np.arange(int(seconds * rate)) / 50) * 8000).astype('<i2')
    with wave.open(str(path), 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(data.tobytes())
    return data


def test_read_wav_float32_round_trip(tmp_path):
    path = tmp_path / 'a.wav'
    data = _write_wav(path, 0.5)
    samples, rate = pt.read_wav_float32(path)
    assert rate == 16000 and samples.dtype == np.float32
    np.testing.assert_allclose(samples, data / 32768.0)


def test_read_wav_float32_rejects_non_int16(tmp_path):
    path = tmp_path / 'b.wav'
    with wave.open(str(path), 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(1)
        wf.setframerate(16000)
        wf.writeframes(b'\x80' * 100)
    with pytest.raises(ValueError):
        pt.read_wav_float32(path)


class _Result:
    def __init__(self, tokens, timestamps):
        self.tokens, self.timestamps = tokens, timestamps


class _FakeModel:
    def __init__(self):
        self.calls = []

    def recognize(self, chunk, sample_rate):
        self.calls.append(len(chunk) / sample_rate)
        if len(self.calls) == 1:
            # 'edge' at 28.5 s absolute sits in the overlap; first window core ends at 29.
            return _Result([M + 'one', M + 'edge'], [1.0, 28.5])
        # Second window starts at 28 s: 'edge' reappears at 0.5 s (start 28.5, still the overlap), 'two' at 2.0 s.
        return _Result([M + 'edge', M + 'two.', M + '\u043f\u0440\u0438'], [0.5, 2.0, 2.5])


def test_decode_dedupes_overlap_and_offsets(tmp_path, monkeypatch):
    path = tmp_path / 'c.wav'
    _write_wav(path, 31.0)
    model = _FakeModel()
    monkeypatch.setattr(pt.ParakeetModelSingleton, 'get_instance', classmethod(lambda cls: model))
    segs = pt.decode(path, 'en')
    words = [w for s in segs for w in s['words']]
    assert [w['word'] for w in words] == ['one', 'edge', 'two.']
    assert words[1]['start'] == 28.5
    assert words[2]['start'] == 30.0
    assert model.calls == [30.0, 3.0]


def test_decode_keeps_non_latin_for_auto(tmp_path, monkeypatch):
    path = tmp_path / 'd.wav'
    _write_wav(path, 3.0)
    model = types.SimpleNamespace(
        recognize=lambda chunk, sample_rate: _Result([M + '\u043f\u0440\u0438'], [0.1]))
    monkeypatch.setattr(pt.ParakeetModelSingleton, 'get_instance', classmethod(lambda cls: model))
    assert pt.decode(path, 'auto')[0]['text'] == '\u043f\u0440\u0438'
    assert pt.decode(path, 'en') == []



@pytest.fixture
def clean_singleton(monkeypatch, tmp_path):
    monkeypatch.setenv('HF_HOME', str(tmp_path))
    pt.ParakeetModelSingleton.unload_model()
    yield
    pt.ParakeetModelSingleton.unload_model()


class _Clock:
    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self):
        return self.now


def _real_hub_call(*a, **k):
    pytest.fail('a test reached the real Hugging Face hub; stub it with _install_hub')


@pytest.fixture(autouse=True)
def no_real_hub(monkeypatch):
    """Fail any test that reaches the real hub or sends a real HTTP request; pytest.fail escapes except Exception."""
    for name in ('snapshot_download', 'hf_hub_download'):
        monkeypatch.setattr(huggingface_hub, name, _real_hub_call)
    monkeypatch.setattr(huggingface_hub.HfApi, 'model_info', _real_hub_call)
    monkeypatch.setattr(httpx.Client, 'send', _real_hub_call)
    monkeypatch.setattr(httpx.AsyncClient, 'send', _real_hub_call)
    monkeypatch.setattr(socket.socket, 'connect', _real_hub_call)
    monkeypatch.setattr(socket.socket, 'connect_ex', _real_hub_call)


def test_real_hub_calls_fail_the_test():
    for call in (lambda: huggingface_hub.snapshot_download('x/y'),
                 lambda: huggingface_hub.HfApi().model_info('x/y', timeout=1),
                 lambda: huggingface_hub.hf_hub_download('x/y', 'f.txt'),
                 lambda: socket.socket().connect(('192.0.2.1', 9)),
                 lambda: httpx.Client().get('https://example.com/')):
        with pytest.raises(pytest.fail.Exception):
            call()


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    fake = _Clock()
    monkeypatch.setattr(pt, '_clock', fake)
    monkeypatch.setattr(pt, '_last_failed_check', {})
    monkeypatch.setattr(pt, '_rechecks_running', set())
    monkeypatch.setattr(pt, '_recheck_runner', lambda work: work())
    monkeypatch.setattr(pt, '_free_bytes', lambda path: 1 << 50)
    monkeypatch.setattr(pt, '_updates_off', set())
    monkeypatch.setattr(pt, '_downloads_started', {})
    monkeypatch.delenv('HF_HUB_OFFLINE', raising=False)
    return fake


SHA_OLD, SHA_NEW = 'a' * 40, 'b' * 40
WEEK = pt.PARAKEET_MODEL_RECHECK_SECONDS
HOUR = pt.PARAKEET_MODEL_RETRY_SECONDS
REPO_FILES = ['README.md', 'config.json', 'vocab.txt', 'encoder-model.onnx', 'encoder-model.onnx.data',
              'decoder_joint-model.onnx', 'encoder-model.int8.onnx', 'decoder_joint-model.int8.onnx']


class _Hub:
    """huggingface_hub stand-in shaped like the real one: model_info raises, snapshot_download may change nothing."""

    def __init__(self, sha=SHA_OLD, probe_error=None, write=None, snapshot_error=None, siblings=REPO_FILES):
        self.sha, self.probe_error, self.write, self.snapshot_error = sha, probe_error, write, snapshot_error
        self.siblings = siblings
        self.probes, self.snapshots = [], []

    def model_info(self, repo, timeout=None):
        self.probes.append({'repo': repo, 'timeout': timeout})
        if self.probe_error:
            raise self.probe_error
        return types.SimpleNamespace(
            sha=self.sha, siblings=[types.SimpleNamespace(rfilename=n) for n in self.siblings])

    def snapshot_download(self, repo, revision=None, local_dir=None, allow_patterns=None):
        self.snapshots.append({'repo': repo, 'revision': revision, 'local_dir': local_dir,
                               'allow_patterns': allow_patterns})
        if self.write:
            self.write(Path(local_dir))
        if self.snapshot_error:
            raise self.snapshot_error
        return local_dir


def _install_hub(monkeypatch, hub):
    mod = types.ModuleType('huggingface_hub')
    mod.snapshot_download = hub.snapshot_download
    mod.HfApi = lambda: hub
    monkeypatch.setitem(sys.modules, 'huggingface_hub', mod)
    return hub


def _fake_modules(monkeypatch, load_model, preload=None, hub=None):
    hub = _install_hub(monkeypatch, hub or _Hub(write=lambda d: d.mkdir(parents=True, exist_ok=True)))
    onnx_asr = types.ModuleType('onnx_asr')
    onnx_asr.load_model = load_model
    ort = types.ModuleType('onnxruntime')
    if preload:
        ort.preload_dlls = preload
    monkeypatch.setitem(sys.modules, 'onnx_asr', onnx_asr)
    monkeypatch.setitem(sys.modules, 'onnxruntime', ort)
    return hub.snapshots


def _write_model_files(model_dir, quantization=None, sizes=None, skip=()):
    sfx = f'.{quantization}' if quantization else ''
    names = ['config.json', 'vocab.txt', f'encoder-model{sfx}.onnx', f'decoder_joint-model{sfx}.onnx']
    if not quantization:
        names.append('encoder-model.onnx.data')
    model_dir.mkdir(parents=True, exist_ok=True)
    for n in names:
        if n not in skip:
            (model_dir / n).write_bytes(b'x' * (sizes or {}).get(n, 4))
    return names


def test_get_instance_cuda_and_cpu_settings(monkeypatch, clean_singleton):
    seen = {}
    preloaded = []

    class _Loaded:
        def with_timestamps(self):
            return 'ts-model'

    def load_model(name, quantization=None, providers=None, path=None):
        seen.update(name=name, quantization=quantization, providers=providers)
        return _Loaded()

    snaps = _fake_modules(monkeypatch, load_model, preload=lambda: preloaded.append(1))
    monkeypatch.setattr(pt, 'resolve_whisper_device', lambda: 'cuda')
    assert pt.ParakeetModelSingleton.get_instance() == 'ts-model'
    assert pt.ParakeetModelSingleton.is_loaded()
    assert seen == {'name': pt.PARAKEET_MODEL_NAME, 'quantization': None,
                    'providers': ['CUDAExecutionProvider', 'CPUExecutionProvider']}
    assert preloaded == [1]
    pt.ParakeetModelSingleton.mark_for_reload()
    monkeypatch.setattr(pt, 'resolve_whisper_device', lambda: 'cpu')
    pt.ParakeetModelSingleton.get_instance()
    assert seen['quantization'] == 'int8' and seen['providers'] == ['CPUExecutionProvider']
    cuda_patterns, cpu_patterns = snaps[0]['allow_patterns'], snaps[1]['allow_patterns']
    assert 'encoder-model.onnx' in cuda_patterns and 'encoder-model.onnx?data' in cuda_patterns
    assert 'encoder-model?int8.onnx' in cpu_patterns and 'decoder_joint-model?int8.onnx?data' in cpu_patterns
    assert snaps[0]['repo'] == pt.PARAKEET_MODEL_REPO


def test_get_instance_wraps_load_failure(monkeypatch, clean_singleton):
    def boom(*a, **k):
        raise RuntimeError('no weights')

    _fake_modules(monkeypatch, boom)
    monkeypatch.setattr(pt, 'resolve_whisper_device', lambda: 'cpu')
    with pytest.raises(ModelLoadError, match='no weights'):
        pt.ParakeetModelSingleton.get_instance()
    assert not pt.ParakeetModelSingleton.is_loaded()


def test_parakeet_available_follows_find_spec(monkeypatch):
    monkeypatch.setattr(pt.importlib.util, 'find_spec', lambda name: None)
    assert pt.parakeet_available() is False
    assert 'onnx-asr' in pt.parakeet_unavailable_message()
    monkeypatch.setattr(pt.importlib.util, 'find_spec', lambda name: object())
    assert pt.parakeet_available() is True


def _boundary_model(first_ts, second_ts, first_word='edge', second_word='edge'):
    """Fake model emitting the word 'edge' at an absolute time per window (window 1 starts at 28 s)."""
    calls = []

    def recognize(chunk, sample_rate):
        calls.append(1)
        if len(calls) == 1:
            return _Result([M + 'one', M + first_word], [1.0, first_ts])
        return _Result([M + second_word, M + 'two.'], [second_ts - 28.0, 31.5 - 28.0])

    return types.SimpleNamespace(recognize=recognize)


@pytest.mark.parametrize('first_ts,second_ts', [(28.96, 29.04), (29.04, 28.96)])
def test_decode_boundary_word_emitted_once(tmp_path, monkeypatch, first_ts, second_ts):
    path = tmp_path / 'e.wav'
    _write_wav(path, 33.0)
    model = _boundary_model(first_ts, second_ts)
    monkeypatch.setattr(pt.ParakeetModelSingleton, 'get_instance', classmethod(lambda cls: model))
    words = [w['word'] for s in pt.decode(path, 'en') for w in s['words']]
    assert words == ['one', 'edge', 'two.']


def test_decode_boundary_dedupe_ignores_case_and_punctuation(tmp_path, monkeypatch):
    path = tmp_path / 'e.wav'
    _write_wav(path, 33.0)
    model = _boundary_model(28.96, 29.04, 'Edge,', 'edge')
    monkeypatch.setattr(pt.ParakeetModelSingleton, 'get_instance', classmethod(lambda cls: model))
    words = [w['word'] for s in pt.decode(path, 'en') for w in s['words']]
    assert len(words) == 3 and sum(w.casefold().strip(',.') == 'edge' for w in words) == 1


def test_words_from_tokens_length_mismatch_uses_shorter(caplog):
    with caplog.at_level('WARNING', logger='podcast.parakeet'):
        words = pt.words_from_tokens([M + 'a', M + 'b', M + 'c'], [0.0, 0.5])
    assert [w['word'] for w in words] == ['a', 'b']
    assert '3 tokens but 2 timestamps' in caplog.text


def test_get_instance_loads_into_plain_dir_under_hf_home(monkeypatch, tmp_path, clean_singleton):
    seen = {}

    class _Loaded:
        def with_timestamps(self):
            return 'ts-model'

    def load_model(name, quantization=None, providers=None, path=None):
        seen['path'] = path
        return _Loaded()

    snaps = _fake_modules(monkeypatch, load_model)
    monkeypatch.setattr(pt, 'resolve_whisper_device', lambda: 'cpu')
    pt.ParakeetModelSingleton.get_instance()
    expected = str(tmp_path / 'parakeet' / pt.PARAKEET_MODEL_NAME)
    assert snaps[0]['local_dir'] == expected
    assert seen['path'] == expected
    assert os.path.isdir(expected)


def test_words_from_tokens_accepts_sentencepiece_marker_and_skips_specials():
    # A bare marker opens an empty word that is dropped when nothing fills it.
    words = pt.words_from_tokens([MARK + 'Hi', '<unk>', MARK, MARK + 'there', '<|en|>'],
                                 [0.0, 0.1, 0.2, 0.4, 0.5])
    assert [w['word'] for w in words] == ['Hi', 'there']


def test_decode_window_of_space_prefixed_tokens_splits_into_segments(tmp_path, monkeypatch):
    path = tmp_path / 'f.wav'
    _write_wav(path, 30.0)
    toks = [' a', ' b', ' c', ' d', ' e', ' f', ' g.', ' h', ' i', ' j', ' k', ' l.']
    stamps = [i * 2.0 for i in range(12)]
    model = types.SimpleNamespace(recognize=lambda chunk, sample_rate: _Result(toks, stamps))
    monkeypatch.setattr(pt.ParakeetModelSingleton, 'get_instance', classmethod(lambda cls: model))
    segs = pt.decode(path, 'en')
    assert [len(s['words']) for s in segs] == [7, 5]
    assert [w['start'] for w in segs[1]['words']] == stamps[7:]


def test_words_from_tokens_bare_marker_starts_digit_word():
    words = pt.words_from_tokens([' to', ' ', '1', '4', ' inches', ' '], [0.0, 0.3, 0.4, 0.5, 0.8, 1.0])
    assert [w['word'] for w in words] == ['to', '14', 'inches']
    assert words[1]['start'] == 0.3
    assert words[0]['end'] == 0.3


def _ws(*pairs):
    return [{'word': w, 'start': t, 'end': t + 0.25} for w, t in pairs]


def test_merge_windows_drops_text_repeat_with_drifted_times():
    a = _ws(('so', 754.0), ('your', 755.3), ('whole', 755.6), ('body', 755.9))
    b = _ws(('your', 755.8), ('whole', 756.2), ('body', 756.5), ('can', 756.9), ('feel', 757.2))
    merged = pt._merge_windows([(0.0, 756.0, a), (756.0, 790.0, b)])
    assert ' '.join(w['word'] for w in merged) == 'so your whole body can feel'


def test_merge_windows_keeps_genuinely_new_leading_words():
    a = _ws(('your', 755.3), ('whole', 755.6), ('body', 755.9))
    b = _ws(('the', 756.2), ('difference', 756.6))
    merged = pt._merge_windows([(0.0, 756.0, a), (756.0, 790.0, b)])
    assert ' '.join(w['word'] for w in merged) == 'your whole body the difference'


def test_merge_windows_dedupes_past_a_drifted_earlier_word():
    a = _ws(('so', 754.0), ('your', 755.3), ('whole', 755.6), ('body', 755.9), ('can', 756.1))
    b = _ws(('uh', 755.9), ('can', 756.15), ('feel', 757.2))
    merged = pt._merge_windows([(0.0, 756.0, a), (756.0, 790.0, b)])
    assert [w['word'] for w in merged].count('can') == 1
    assert merged[-1]['word'] == 'feel'


def _loaded_with_providers(providers):
    session = types.SimpleNamespace(get_providers=lambda: providers)
    return types.SimpleNamespace(with_timestamps=lambda: types.SimpleNamespace(asr=types.SimpleNamespace(_encoder=session)))


def _load_on(monkeypatch, loaded, device):
    _fake_modules(monkeypatch, lambda *a, **k: loaded)
    monkeypatch.setattr(pt, 'resolve_whisper_device', lambda: device)
    return pt.ParakeetModelSingleton.get_instance()


def _provider_records(caplog):
    return [r for r in caplog.records if 'CUDA' in r.getMessage() and r.levelno >= logging.WARNING]


def test_cuda_in_use_logs_no_fallback_warning(monkeypatch, clean_singleton, caplog):
    with caplog.at_level(logging.DEBUG, logger=pt.logger.name):
        _load_on(monkeypatch, _loaded_with_providers(['CUDAExecutionProvider', 'CPUExecutionProvider']), 'cuda')
    assert _provider_records(caplog) == []


def test_cpu_fallback_on_cuda_logs_one_warning(monkeypatch, clean_singleton, caplog):
    with caplog.at_level(logging.DEBUG, logger=pt.logger.name):
        _load_on(monkeypatch, _loaded_with_providers(['CPUExecutionProvider']), 'cuda')
    records = _provider_records(caplog)
    assert [r.levelno for r in records] == [logging.WARNING]
    assert 'CPUExecutionProvider' in records[0].getMessage()


@pytest.mark.parametrize('providers', [None, [1, 2], 'CPUExecutionProvider'], ids=['none', 'non-strings', 'string'])
def test_unexpected_provider_list_never_fails_the_load(monkeypatch, clean_singleton, providers):
    model = _load_on(monkeypatch, _loaded_with_providers(providers), 'cuda')
    assert model is not None and pt.ParakeetModelSingleton.is_loaded()


def test_cpu_device_never_checks_providers(monkeypatch, clean_singleton, caplog):
    with caplog.at_level(logging.DEBUG, logger=pt.logger.name):
        _load_on(monkeypatch, _loaded_with_providers(['CPUExecutionProvider']), 'cpu')
    assert _provider_records(caplog) == []


def test_missing_session_attribute_logs_one_debug_and_loads(monkeypatch, clean_singleton, caplog):
    loaded = types.SimpleNamespace(with_timestamps=lambda: types.SimpleNamespace())
    with caplog.at_level(logging.DEBUG, logger=pt.logger.name):
        model = _load_on(monkeypatch, loaded, 'cuda')
    assert model is not None and pt.ParakeetModelSingleton.is_loaded()
    unreadable = [r for r in caplog.records if 'providers' in r.getMessage().lower()
                  and 'could not' in r.getMessage().lower()]
    assert [r.levelno for r in unreadable] == [logging.DEBUG]


def test_get_instance_sets_ort_severity_before_loading_and_keeps_errors(monkeypatch, clean_singleton):
    order = []
    loaded = types.SimpleNamespace(with_timestamps=lambda: 'ts-model')
    _fake_modules(monkeypatch, lambda *a, **k: order.append('load') or loaded)
    sys.modules['onnxruntime'].set_default_logger_severity = lambda level: order.append(('severity', level))
    monkeypatch.setattr(pt, 'resolve_whisper_device', lambda: 'cpu')
    pt.ParakeetModelSingleton.get_instance()
    assert order == [('severity', 3), 'load']


def test_get_instance_tolerates_onnxruntime_without_severity_api(monkeypatch, clean_singleton):
    loaded = types.SimpleNamespace(with_timestamps=lambda: 'ts-model')
    _fake_modules(monkeypatch, lambda *a, **k: loaded)
    monkeypatch.setattr(pt, 'resolve_whisper_device', lambda: 'cpu')
    assert pt.ParakeetModelSingleton.get_instance() == 'ts-model'


# First download and the completion record, driven through _fetch_model_files.

def _mdir(tmp_path):
    return tmp_path / 'parakeet' / 'model'


def _fetch(tmp_path, quantization=None):
    return pt._fetch_model_files(str(_mdir(tmp_path)), quantization)[1]


def _record(model_dir, quantization='int8'):
    return json.loads(Path(pt._record_path(str(model_dir), quantization)).read_text())


def _rewrite_record(model_dir, edit, quantization=None):
    path = Path(pt._record_path(str(model_dir), quantization))
    path.write_text(json.dumps(edit(json.loads(path.read_text()))))


def _writing_hub(monkeypatch, tmp_path, quantization=None, **kw):
    return _install_hub(monkeypatch, _Hub(write=lambda d: _write_model_files(d, quantization), **kw))


@pytest.mark.parametrize('quantization', [None, 'int8'])
def test_fetch_skips_download_after_a_completed_fetch(tmp_path, monkeypatch, quantization):
    hub = _writing_hub(monkeypatch, tmp_path, quantization)
    _fetch(tmp_path, quantization)
    assert _fetch(tmp_path, quantization) is False
    assert len(hub.snapshots) == 1


def test_first_download_records_repo_sha_files_and_checked_at(tmp_path, monkeypatch, clock):
    hub = _writing_hub(monkeypatch, tmp_path)
    _fetch(tmp_path)
    assert hub.probes == [{'repo': pt.PARAKEET_MODEL_REPO, 'timeout': pt.PARAKEET_MODEL_PROBE_TIMEOUT_SECONDS}]
    assert hub.snapshots[0]['revision'] == SHA_OLD and hub.snapshots[0]['local_dir'] == str(_mdir(tmp_path))
    record = _record(_mdir(tmp_path), None)
    assert record == {'repo': pt.PARAKEET_MODEL_REPO, 'sha': SHA_OLD, 'checked_at': clock.now,
                      'files': {n: 4 for n in _write_model_files(tmp_path / 'scratch')}}


def test_first_download_with_failed_probe_records_nothing_and_retries_after_the_hour(tmp_path, monkeypatch, clock):
    hub = _writing_hub(monkeypatch, tmp_path, probe_error=httpx.ConnectError('down'))
    _fetch(tmp_path)
    assert hub.snapshots[0]['revision'] is None
    assert not list(_mdir(tmp_path).glob('.minuspod-complete*'))
    _fetch(tmp_path)
    assert len(hub.probes) == 1
    clock.now += HOUR
    hub.probe_error = None
    _fetch(tmp_path)
    assert len(hub.probes) == 2 and _record(_mdir(tmp_path), None)['sha'] == SHA_OLD


def test_failed_probe_with_a_required_file_missing_writes_no_record(tmp_path, monkeypatch, caplog):
    _write_model_files(_mdir(tmp_path), skip=('vocab.txt',))
    hub = _install_hub(monkeypatch, _Hub(probe_error=httpx.ConnectError('down')))
    with caplog.at_level(logging.WARNING, logger=pt.logger.name):
        assert pt._fetch_model_files(str(_mdir(tmp_path)), None) == (False, False)
    assert not list(_mdir(tmp_path).glob('.minuspod-complete*'))
    assert len(hub.snapshots) == 1 and 'not recorded' in caplog.text


@pytest.mark.parametrize('quantization', [None, 'int8'])
def test_failed_probe_over_complete_files_records_them_without_sha_and_probes_once(
        tmp_path, monkeypatch, clock, caplog, quantization):
    _write_model_files(_mdir(tmp_path), quantization)
    hub = _install_hub(monkeypatch, _Hub(probe_error=httpx.ConnectError('down')))
    with caplog.at_level(logging.WARNING, logger=pt.logger.name):
        assert pt._fetch_model_files(str(_mdir(tmp_path)), quantization) == (True, False)
    record = _record(_mdir(tmp_path), quantization)
    assert 'sha' not in record and 'checked_at' not in record
    assert hub.snapshots == [] and 'ConnectError' in caplog.text
    for _ in range(3):
        clock.now += HOUR
        assert _fetch(tmp_path, quantization) is True
    assert len(hub.probes) == 1


def test_first_download_missing_a_listed_file_records_nothing(tmp_path, monkeypatch, caplog):
    _install_hub(monkeypatch, _Hub(write=lambda d: _write_model_files(d, skip=('encoder-model.onnx.data',))))
    with caplog.at_level(logging.WARNING, logger=pt.logger.name):
        _fetch(tmp_path)
    assert not list(_mdir(tmp_path).glob('.minuspod-complete*'))
    assert 'not recorded' in caplog.text


def test_fetch_downloads_when_files_exist_without_a_completion_record(tmp_path, monkeypatch):
    _write_model_files(_mdir(tmp_path))
    hub = _install_hub(monkeypatch, _Hub())
    _fetch(tmp_path)
    assert len(hub.snapshots) == 1


@pytest.mark.parametrize('missing', ['vocab.txt', 'decoder_joint-model.onnx', 'encoder-model.onnx.data'])
def test_fetch_downloads_when_a_recorded_file_is_gone(tmp_path, monkeypatch, missing):
    hub = _writing_hub(monkeypatch, tmp_path)
    _fetch(tmp_path)
    (_mdir(tmp_path) / missing).unlink()
    _fetch(tmp_path)
    assert len(hub.snapshots) == 2


def test_fetch_downloads_when_a_file_was_truncated_on_disk(tmp_path, monkeypatch):
    hub = _install_hub(monkeypatch, _Hub(write=lambda d: _write_model_files(d, sizes={'encoder-model.onnx.data': 100})))
    _fetch(tmp_path)
    (_mdir(tmp_path) / 'encoder-model.onnx.data').write_bytes(b'x' * 40)
    _fetch(tmp_path)
    assert len(hub.snapshots) == 2


def test_fetch_records_nothing_when_the_download_left_files_missing(tmp_path, monkeypatch):
    hub = _install_hub(monkeypatch, _Hub())
    _fetch(tmp_path)
    _fetch(tmp_path)
    assert len(hub.snapshots) == 2
    assert not list(_mdir(tmp_path).glob('.minuspod-complete*'))


def test_get_instance_offline_uses_local_files(monkeypatch, tmp_path, clean_singleton):
    _write_model_files(tmp_path / 'parakeet' / pt.PARAKEET_MODEL_NAME, 'int8')
    loaded = types.SimpleNamespace(with_timestamps=lambda: 'ts-model')
    hub = _Hub(probe_error=httpx.ConnectError('down'))
    _fake_modules(monkeypatch, lambda *a, **k: loaded, hub=hub)
    monkeypatch.setattr(pt, 'resolve_whisper_device', lambda: 'cpu')
    assert pt.ParakeetModelSingleton.get_instance() == 'ts-model'


def test_get_instance_offline_without_local_copy_fails(monkeypatch, clean_singleton):
    loaded = types.SimpleNamespace(with_timestamps=lambda: 'ts-model')
    hub = _Hub(probe_error=httpx.ConnectError('down'), snapshot_error=OSError('no network'))
    _fake_modules(monkeypatch, lambda *a, **k: loaded, hub=hub)
    monkeypatch.setattr(pt, 'resolve_whisper_device', lambda: 'cpu')
    with pytest.raises(ModelLoadError, match='no network'):
        pt.ParakeetModelSingleton.get_instance()


def test_incomplete_local_copy_with_a_failed_download_raises_the_download_error(tmp_path, monkeypatch):
    _mdir(tmp_path).mkdir(parents=True)
    (_mdir(tmp_path) / 'encoder-model.int8.onnx').write_bytes(b'x')
    _install_hub(monkeypatch, _Hub(snapshot_error=OSError('no network')))
    with pytest.raises(OSError, match='no network'):
        _fetch(tmp_path, 'int8')
    assert not list(_mdir(tmp_path).glob('.minuspod-complete*'))


def test_completion_record_is_per_quantization(tmp_path, monkeypatch):
    hub = _writing_hub(monkeypatch, tmp_path, 'int8')
    _fetch(tmp_path, 'int8')
    _fetch(tmp_path, None)
    assert len(hub.snapshots) == 2


def test_unreadable_completion_record_means_download(tmp_path, monkeypatch):
    hub = _writing_hub(monkeypatch, tmp_path)
    _fetch(tmp_path)
    for rec in _mdir(tmp_path).glob('.minuspod-complete*'):
        rec.write_text('{not json')
    _fetch(tmp_path)
    assert len(hub.snapshots) == 2


@pytest.mark.parametrize('edit', [lambda r: {**r, 'repo': 'someone/else'}, lambda r: r['files']],
                         ids=['other-repo', 'old-format'])
def test_unusable_record_means_download(tmp_path, monkeypatch, edit):
    hub = _writing_hub(monkeypatch, tmp_path)
    _fetch(tmp_path)
    _rewrite_record(_mdir(tmp_path), edit)
    _fetch(tmp_path)
    assert len(hub.snapshots) == 2


@pytest.mark.parametrize('failing', ['replace', 'dump'])
def test_failed_record_write_leaves_no_temp_file_and_the_fetch_succeeds(tmp_path, monkeypatch, failing):
    def boom(*a, **k):
        raise OSError('read-only file system')

    target = (pt.os, 'replace') if failing == 'replace' else (pt.json, 'dump')
    hub = _writing_hub(monkeypatch, tmp_path)
    monkeypatch.setattr(*target, boom)
    _fetch(tmp_path)
    assert len(hub.snapshots) == 1
    assert not list(_mdir(tmp_path).glob('.minuspod-complete*'))


def test_file_vanishing_before_the_record_stat_does_not_fail_the_fetch(tmp_path, monkeypatch):
    hub = _writing_hub(monkeypatch, tmp_path)
    real = os.path.getsize

    def vanish(path):
        if str(path).endswith('vocab.txt'):
            raise FileNotFoundError(path)
        return real(path)
    monkeypatch.setattr(pt.os.path, 'getsize', vanish)
    _fetch(tmp_path)
    assert len(hub.snapshots) == 1
    assert not list(_mdir(tmp_path).glob('.minuspod-complete*'))


def test_fresh_record_is_not_due_until_a_week(tmp_path, monkeypatch, clock):
    _writing_hub(monkeypatch, tmp_path)
    _fetch(tmp_path)
    clock.now += WEEK - 1
    assert _fetch(tmp_path) is False
    clock.now += 1
    assert _fetch(tmp_path) is True


@pytest.mark.parametrize('edit', [lambda r: r.pop('checked_at'), lambda r: r.update(checked_at='soon'),
                                  lambda r: r.update(checked_at=9e9)],
                         ids=['missing', 'not-a-number', 'future'])
def test_unusable_checked_at_counts_as_due_not_incomplete(tmp_path, monkeypatch, edit):
    hub = _writing_hub(monkeypatch, tmp_path)
    _fetch(tmp_path)
    _rewrite_record(_mdir(tmp_path), lambda rec: (edit(rec), rec)[1])
    assert _fetch(tmp_path) is True
    assert len(hub.snapshots) == 1


def test_skip_path_does_not_touch_the_hub(tmp_path, monkeypatch):
    _writing_hub(monkeypatch, tmp_path)
    _fetch(tmp_path)
    monkeypatch.delitem(sys.modules, 'huggingface_hub')
    monkeypatch.setattr(sys, 'meta_path', [])
    assert _fetch(tmp_path) is False


# Weekly re-check, driven end to end through get_instance with a synchronous runner.

def _live(tmp_path):
    return tmp_path / 'parakeet' / pt.PARAKEET_MODEL_NAME


def _tree(path):
    return {str(p.relative_to(path)): p.read_bytes() for p in sorted(path.rglob('*')) if p.is_file()}


def _load(monkeypatch, device='cpu', load_model=None):
    loaded = types.SimpleNamespace(with_timestamps=lambda: 'ts-model')
    onnx_asr = types.ModuleType('onnx_asr')
    onnx_asr.load_model = load_model or (lambda *a, **k: loaded)
    monkeypatch.setitem(sys.modules, 'onnx_asr', onnx_asr)
    monkeypatch.setitem(sys.modules, 'onnxruntime', types.ModuleType('onnxruntime'))
    monkeypatch.setattr(pt, 'resolve_whisper_device', lambda: device)
    pt.ParakeetModelSingleton.unload_model()
    return pt.ParakeetModelSingleton.get_instance()


def _prime(monkeypatch, tmp_path, device='cpu'):
    q = None if device == 'cuda' else 'int8'
    _install_hub(monkeypatch, _Hub(write=lambda d: _write_model_files(d, q)))
    _load(monkeypatch, device)
    return _tree(_live(tmp_path))


def _warnings(caplog):
    return [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]


@pytest.fixture
def primed(monkeypatch, tmp_path, clean_singleton, clock):
    before = _prime(monkeypatch, tmp_path)
    clock.now += WEEK
    return before


def test_due_check_probes_with_the_timeout_and_same_sha_downloads_nothing(monkeypatch, tmp_path, primed, clock):
    hub = _install_hub(monkeypatch, _Hub(sha=SHA_OLD))
    assert _load(monkeypatch) == 'ts-model'
    assert hub.probes == [{'repo': pt.PARAKEET_MODEL_REPO, 'timeout': 10}]
    assert hub.snapshots == []
    record = _record(_live(tmp_path))
    assert record['sha'] == SHA_OLD and record['checked_at'] == clock.now
    assert {k: v for k, v in _tree(_live(tmp_path)).items() if not k.startswith('.')} == \
        {k: v for k, v in primed.items() if not k.startswith('.')}


def test_load_that_is_not_due_makes_no_network_call(monkeypatch, tmp_path, clean_singleton, clock):
    _prime(monkeypatch, tmp_path)
    clock.now += WEEK - 1
    hub = _install_hub(monkeypatch, _Hub())
    _load(monkeypatch)
    assert hub.probes == [] and hub.snapshots == []


@pytest.mark.parametrize('error', [
    httpx.ConnectError('https://huggingface.co/api/models/x?token=SECRET'),
    httpx.ReadTimeout('timed out reading ?token=SECRET'),
    HfHubHTTPError('503 for ?token=SECRET', response=httpx.Response(
        503, request=httpx.Request('GET', 'https://huggingface.co/api/models/x?token=SECRET'))),
    OfflineModeIsEnabled('offline'),
    RuntimeError('SECRET'),
], ids=['connect', 'timeout', 'http', 'offline', 'generic'])
def test_failed_probe_loads_the_local_copy_and_keeps_everything(monkeypatch, tmp_path, primed, caplog, error):
    hub = _install_hub(monkeypatch, _Hub(probe_error=error))
    with caplog.at_level(logging.INFO, logger=pt.logger.name):
        assert _load(monkeypatch) == 'ts-model'
    warnings = _warnings(caplog)
    assert len(warnings) == 1 and type(error).__name__ in warnings[0]
    assert 'SECRET' not in caplog.text
    assert hub.snapshots == []
    assert _tree(_live(tmp_path)) == primed


def test_probe_without_a_sha_counts_as_a_failed_check(monkeypatch, tmp_path, primed, caplog):
    _install_hub(monkeypatch, _Hub(sha=None))
    with caplog.at_level(logging.WARNING, logger=pt.logger.name):
        _load(monkeypatch)
    assert 'ValueError' in caplog.text
    assert _tree(_live(tmp_path)) == primed


def test_failed_check_is_not_retried_within_the_hour_but_is_after(monkeypatch, tmp_path, primed, clock):
    hub = _install_hub(monkeypatch, _Hub(probe_error=httpx.ConnectError('down')))
    _load(monkeypatch)
    clock.now += HOUR - 1
    _load(monkeypatch)
    assert len(hub.probes) == 1
    clock.now += 1
    _load(monkeypatch)
    assert len(hub.probes) == 2


def test_record_without_sha_goes_through_the_staged_download(monkeypatch, tmp_path, primed, clock, caplog):
    _rewrite_record(_live(tmp_path), lambda r: {k: v for k, v in r.items() if k != 'sha'}, 'int8')
    hub = _install_hub(monkeypatch, _Hub(sha=SHA_OLD, write=lambda d: _write_model_files(
        d, 'int8', sizes={'vocab.txt': 9})))
    with caplog.at_level(logging.INFO, logger=pt.logger.name):
        _load(monkeypatch)
    assert [s['local_dir'] for s in hub.snapshots] == [f'{_live(tmp_path)}.staging']
    record = _record(_live(tmp_path))
    assert record['sha'] == SHA_OLD and record['checked_at'] == clock.now and record['files']['vocab.txt'] == 9
    assert 'adopting' not in caplog.text and not _warnings(caplog)


def test_same_sha_with_an_unwritable_record_is_a_failed_check(monkeypatch, tmp_path, primed, caplog):
    monkeypatch.setattr(pt, '_write_record', lambda *a: False)
    hub = _install_hub(monkeypatch, _Hub(sha=SHA_OLD))
    with caplog.at_level(logging.WARNING, logger=pt.logger.name):
        _load(monkeypatch)
    assert len(_warnings(caplog)) == 1 and hub.snapshots == []
    assert _tree(_live(tmp_path)) == primed
    _load(monkeypatch)
    assert len(hub.probes) == 1


def test_new_revision_with_complete_staging_is_swapped_in(monkeypatch, tmp_path, primed, clock):
    hub = _install_hub(monkeypatch, _Hub(sha=SHA_NEW, write=lambda d: _write_model_files(
        d, 'int8', sizes={'vocab.txt': 9})))
    _load(monkeypatch)
    live = _live(tmp_path)
    assert hub.snapshots[0]['revision'] == SHA_NEW
    assert hub.snapshots[0]['local_dir'] == f'{live}.staging'
    record = _record(live)
    assert record['sha'] == SHA_NEW and record['checked_at'] == clock.now and record['files']['vocab.txt'] == 9
    assert (live / 'vocab.txt').stat().st_size == 9
    assert not Path(f'{live}.staging').exists() and not Path(f'{live}.old').exists()
    hub2 = _install_hub(monkeypatch, _Hub(sha=SHA_NEW))
    _load(monkeypatch)
    assert hub2.probes == []


def _drop(d, name):
    (d / name).unlink()


@pytest.mark.parametrize('device,write,error', [
    ('cpu', None, None),
    ('cpu', lambda d: _write_model_files(d, 'int8', skip=('vocab.txt',)), None),
    ('cuda', lambda d: _write_model_files(d, skip=('encoder-model.onnx.data',)), None),
    ('cpu', lambda d: (_write_model_files(d, 'int8'), _drop(d, 'decoder_joint-model.int8.onnx')),
     httpx.ReadTimeout('stalled')),
    ('cpu', lambda d: (d.mkdir(parents=True), (d / 'vocab.txt').write_bytes(b'new')), httpx.ConnectError('down')),
], ids=['offline-returns-normally', 'missing-required', 'missing-listed-data-file', 'deletes-in-staging',
        'raises-midway'])
def test_incomplete_staging_leaves_live_byte_for_byte_and_loads_it(
        monkeypatch, tmp_path, clean_singleton, clock, caplog, device, write, error):
    before = _prime(monkeypatch, tmp_path, device)
    clock.now += WEEK
    hub = _install_hub(monkeypatch, _Hub(sha=SHA_NEW, write=write, snapshot_error=error))
    with caplog.at_level(logging.WARNING, logger=pt.logger.name):
        assert _load(monkeypatch, device) == 'ts-model'
    live = _live(tmp_path)
    assert len(hub.snapshots) == 1
    assert _tree(live) == before
    assert not Path(f'{live}.staging').exists() and not Path(f'{live}.old').exists()
    assert len(_warnings(caplog)) == 1
    _load(monkeypatch, device)
    assert len(hub.probes) == 1


def test_low_disk_skips_the_update_with_one_warning(monkeypatch, tmp_path, primed, caplog):
    needed = sum(_record(_live(tmp_path))['files'].values()) + pt.PARAKEET_MODEL_DISK_MARGIN_BYTES
    seen = []
    monkeypatch.setattr(pt, '_free_bytes', lambda path: seen.append(path) or needed - 1)
    hub = _install_hub(monkeypatch, _Hub(sha=SHA_NEW, write=lambda d: _write_model_files(d, 'int8')))
    with caplog.at_level(logging.WARNING, logger=pt.logger.name):
        _load(monkeypatch)
    assert hub.snapshots == [] and seen == [str(tmp_path / 'parakeet')]
    assert len(_warnings(caplog)) == 1 and 'skipped' in _warnings(caplog)[0]
    assert _tree(_live(tmp_path)) == primed
    _load(monkeypatch)
    assert len(hub.probes) == 1


def test_another_worker_holding_the_recheck_lock_means_skip(monkeypatch, tmp_path, primed, caplog):
    hub = _install_hub(monkeypatch, _Hub(sha=SHA_NEW, write=lambda d: _write_model_files(d, 'int8')))
    staging = Path(f'{_live(tmp_path)}.staging')
    staging.mkdir()
    (staging / 'vocab.txt').write_bytes(b'in progress')
    with open(f'{_live(tmp_path)}.recheck.lock', 'a') as other:
        fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with caplog.at_level(logging.WARNING, logger=pt.logger.name):
            assert _load(monkeypatch) == 'ts-model'
    assert hub.probes == [] and _warnings(caplog) == []
    assert _tree(_live(tmp_path)) == primed
    assert (staging / 'vocab.txt').read_bytes() == b'in progress'


def test_one_recheck_at_a_time_in_a_process(monkeypatch, tmp_path, primed):
    pending = []
    monkeypatch.setattr(pt, '_recheck_runner', pending.append)
    hub = _install_hub(monkeypatch, _Hub())
    _load(monkeypatch)
    _load(monkeypatch)
    assert len(pending) == 1
    pending[0]()
    assert len(hub.probes) == 1 and pt._rechecks_running == set()


def _lock_refused(path, mode):
    with open(path, 'a') as f:
        try:
            fcntl.flock(f, mode | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        return False


def test_load_holds_the_shared_lock_while_reading_files(monkeypatch, tmp_path, clean_singleton):
    _prime(monkeypatch, tmp_path)
    lock = f'{_live(tmp_path)}.lock'
    seen = []
    loaded = types.SimpleNamespace(with_timestamps=lambda: 'ts-model')

    def load_model(*a, **k):
        seen.append((_lock_refused(lock, fcntl.LOCK_EX), _lock_refused(lock, fcntl.LOCK_SH)))
        return loaded
    _load(monkeypatch, load_model=load_model)
    assert seen == [(True, False)]


def test_swap_renames_under_the_exclusive_lock(monkeypatch, tmp_path, primed):
    lock = f'{_live(tmp_path)}.lock'
    real = os.rename
    seen = []

    def rename(src, dst):
        seen.append(_lock_refused(lock, fcntl.LOCK_SH))
        real(src, dst)
    monkeypatch.setattr(pt.os, 'rename', rename)
    _install_hub(monkeypatch, _Hub(sha=SHA_NEW, write=lambda d: _write_model_files(d, 'int8')))
    _load(monkeypatch)
    assert seen == [True, True]
    assert _record(_live(tmp_path))['sha'] == SHA_NEW


def test_failed_second_rename_rolls_back_to_the_live_copy(monkeypatch, tmp_path, primed, caplog):
    real = os.rename

    def rename(src, dst):
        if str(src).endswith('.staging'):
            raise OSError('busy')
        real(src, dst)
    monkeypatch.setattr(pt.os, 'rename', rename)
    _install_hub(monkeypatch, _Hub(sha=SHA_NEW, write=lambda d: _write_model_files(d, 'int8', sizes={'vocab.txt': 9})))
    with caplog.at_level(logging.WARNING, logger=pt.logger.name):
        assert _load(monkeypatch) == 'ts-model'
    live = _live(tmp_path)
    assert _tree(live) == primed
    assert not Path(f'{live}.staging').exists() and not Path(f'{live}.old').exists()
    assert len(_warnings(caplog)) == 1 and 'OSError' in _warnings(caplog)[0]


@pytest.mark.parametrize('leftover', ['partial', 'complete'])
def test_leftover_staging_from_a_crash_is_removed_before_the_next_download(
        monkeypatch, tmp_path, primed, clock, leftover):
    staging = Path(f'{_live(tmp_path)}.staging')
    if leftover == 'partial':
        staging.mkdir()
        (staging / 'vocab.txt').write_bytes(b'half')
    else:
        _write_model_files(staging, 'int8', sizes={'vocab.txt': 7})
        pt._record_model_files(str(staging), 'int8', SHA_NEW, clock.now)
    at_start = []
    hub = _install_hub(monkeypatch, _Hub(sha=SHA_NEW, write=lambda d: (
        at_start.append(d.exists()), _write_model_files(d, 'int8', sizes={'vocab.txt': 9}))))
    _load(monkeypatch)
    assert at_start == [False] and len(hub.snapshots) == 1
    assert (_live(tmp_path) / 'vocab.txt').stat().st_size == 9


def test_crash_between_the_renames_restores_the_backup_on_the_next_load(
        monkeypatch, tmp_path, clean_singleton, clock, caplog):
    before = _prime(monkeypatch, tmp_path)
    live = _live(tmp_path)
    os.rename(live, f'{live}.old')
    _write_model_files(Path(f'{live}.staging'), 'int8', sizes={'vocab.txt': 9})
    hub = _install_hub(monkeypatch, _Hub())
    with caplog.at_level(logging.WARNING, logger=pt.logger.name):
        assert _load(monkeypatch) == 'ts-model'
    assert _tree(live) == before and not Path(f'{live}.old').exists()
    assert hub.probes == [] and hub.snapshots == []
    assert any('Restored' in m for m in _warnings(caplog))
    clock.now += WEEK
    _load(monkeypatch)
    assert not Path(f'{live}.staging').exists()


@pytest.mark.parametrize('backup', ['whole', 'partly-removed'])
def test_crash_after_the_swap_leaves_the_new_copy_live_and_the_backup_is_removed(
        monkeypatch, tmp_path, clean_singleton, clock, backup):
    before = _prime(monkeypatch, tmp_path)
    live = _live(tmp_path)
    old = Path(f'{live}.old')
    shutil.copytree(live, old)
    if backup == 'partly-removed':
        (old / 'vocab.txt').unlink()
    hub = _install_hub(monkeypatch, _Hub())
    assert _load(monkeypatch) == 'ts-model'
    assert _tree(live) == before and hub.probes == []
    clock.now += WEEK
    _load(monkeypatch)
    assert not old.exists() and len(hub.probes) == 1


def test_hub_import_failure_during_a_due_check_still_loads(monkeypatch, tmp_path, primed, caplog):
    monkeypatch.setitem(sys.modules, 'huggingface_hub', None)
    with caplog.at_level(logging.WARNING, logger=pt.logger.name):
        assert _load(monkeypatch) == 'ts-model'
    assert len(_warnings(caplog)) == 1 and 'ModuleNotFoundError' in _warnings(caplog)[0]
    assert _tree(_live(tmp_path)) == primed


def test_runner_that_cannot_start_leaves_no_running_flag(monkeypatch, tmp_path, primed, caplog):
    def refuse(work):
        raise RuntimeError("can't start new thread")
    monkeypatch.setattr(pt, '_recheck_runner', refuse)
    with caplog.at_level(logging.WARNING, logger=pt.logger.name):
        assert _load(monkeypatch) == 'ts-model'
    assert pt._rechecks_running == set() and 'RuntimeError' in caplog.text


def test_default_runner_starts_a_named_daemon_thread():
    seen = []
    thread = pt._start_thread(lambda: seen.append(threading.current_thread().daemon))
    thread.join()
    assert seen == [True] and thread.name == 'parakeet-model-recheck'


def _enolck(*a, **k):
    raise OSError(errno.ENOLCK, 'No locks available')


def test_flock_without_lock_support_lets_a_load_proceed_and_refuses_a_recheck(tmp_path, monkeypatch):
    monkeypatch.setattr(pt.fcntl, 'flock', _enolck)
    model = str(tmp_path / 'm')
    with pt._flock(model, '.lock', fcntl.LOCK_SH) as shared, pt._flock(model, '.lock', fcntl.LOCK_EX) as exclusive:
        assert shared is True and exclusive is True
    with pt._flock(model, '.recheck.lock', fcntl.LOCK_EX | fcntl.LOCK_NB) as held:
        assert held is False


def _infos_off(caplog):
    return [r.getMessage() for r in caplog.records if 'update checks are off' in r.getMessage()]


@pytest.mark.parametrize('failure', ['flock', 'open'])
def test_volume_without_lock_support_logs_one_info_and_starts_no_checks(
        monkeypatch, tmp_path, primed, clock, caplog, failure):
    if failure == 'flock':
        monkeypatch.setattr(pt.fcntl, 'flock', _enolck)
    else:
        real_open = os.open
        monkeypatch.setattr(pt.os, 'open', lambda p, *a, **k: (
            _enolck() if str(p).endswith('.lock') else real_open(p, *a, **k)))
    pending = []
    monkeypatch.setattr(pt, '_recheck_runner', pending.append)
    hub = _install_hub(monkeypatch, _Hub(sha=SHA_NEW, write=lambda d: _write_model_files(d, 'int8')))
    with caplog.at_level(logging.INFO, logger=pt.logger.name):
        for _ in range(3):
            assert _load(monkeypatch) == 'ts-model'
            clock.now += HOUR
    infos = _infos_off(caplog)
    assert len(infos) == 1 and ('file locks' in infos[0] if failure == 'flock' else 'lock file' in infos[0])
    assert 'until the process restarts' in infos[0]
    assert pending == [] and hub.probes == [] and _warnings(caplog) == []
    assert _tree(_live(tmp_path)) == primed


def test_lock_contention_does_not_turn_updates_off(monkeypatch, tmp_path, primed, caplog):
    _install_hub(monkeypatch, _Hub())
    with open(f'{_live(tmp_path)}.recheck.lock', 'a') as other, caplog.at_level(logging.INFO, logger=pt.logger.name):
        fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _load(monkeypatch)
    assert _infos_off(caplog) == [] and pt._updates_off == set()


def _patch_stat_dev(monkeypatch, target):
    real = os.stat

    def fake(path, *a, **k):
        result = real(path, *a, **k)
        if str(path) == target:
            fields = list(result)
            fields[2] = result.st_dev + 1
            return os.stat_result(fields)
        return result
    monkeypatch.setattr(pt.os, 'stat', fake)


@pytest.mark.parametrize('blocker', ['symlink', 'mount', 'other-device'])
def test_model_dir_that_cannot_be_renamed_skips_updates_with_one_info(
        monkeypatch, tmp_path, primed, clock, caplog, blocker):
    live = str(_live(tmp_path))
    if blocker == 'symlink':
        monkeypatch.setattr(pt.os.path, 'islink', lambda p: str(p) == live)
    elif blocker == 'mount':
        monkeypatch.setattr(pt.os.path, 'ismount', lambda p: str(p) == live)
    else:
        _patch_stat_dev(monkeypatch, live)
    hub = _install_hub(monkeypatch, _Hub(sha=SHA_NEW, write=lambda d: _write_model_files(d, 'int8')))
    with caplog.at_level(logging.INFO, logger=pt.logger.name):
        assert _load(monkeypatch) == 'ts-model'
        clock.now += HOUR
        _load(monkeypatch)
    assert hub.probes == [] and hub.snapshots == []
    assert len([r for r in caplog.records if 'update checks are off' in r.getMessage()]) == 1
    assert not _warnings(caplog)
    assert _tree(_live(tmp_path)) == primed


@pytest.mark.parametrize('code', [errno.EBUSY, errno.EXDEV, errno.ENOTDIR, errno.EPERM])
def test_first_rename_refused_turns_updates_off_for_the_process(monkeypatch, tmp_path, primed, clock, caplog, code):
    live = str(_live(tmp_path))
    real = os.rename

    def rename(src, dst):
        if str(src) == live:
            raise OSError(code, os.strerror(code))
        real(src, dst)
    monkeypatch.setattr(pt.os, 'rename', rename)
    hub = _install_hub(monkeypatch, _Hub(sha=SHA_NEW, write=lambda d: _write_model_files(d, 'int8')))
    with caplog.at_level(logging.WARNING, logger=pt.logger.name):
        assert _load(monkeypatch) == 'ts-model'
    assert len(_warnings(caplog)) == 1 and errno.errorcode[code] in _warnings(caplog)[0]
    assert _tree(_live(tmp_path)) == primed and not Path(f'{live}.staging').exists()
    clock.now += WEEK
    _load(monkeypatch)
    assert len(hub.probes) == 1 and len(hub.snapshots) == 1


class NoSuchFile(Exception):
    pass


class InvalidProtobuf(Exception):
    pass


class RuntimeException(Exception):
    pass


class Fail(Exception):
    pass


@pytest.mark.parametrize('error', [
    NoSuchFile('[ONNXRuntimeError] : 3 : NO_SUCHFILE : Load model failed. File does not exist'),
    InvalidProtobuf('[ONNXRuntimeError] : 7 : INVALID_PROTOBUF : Protobuf parsing failed.'),
    RuntimeException('[ONNXRuntimeError] : 6 : RUNTIME_EXCEPTION : Exception during initialization: '
                     'filesystem error: cannot get file size: No such file or directory'),
    Fail('[ONNXRuntimeError] : 1 : FAIL : Deserialize tensor w failed. GetExtDataFromTensorProto '
         'External initializer: w offset: 0 size to read: 64 does not match the file size: 10'),
    FileNotFoundError('vocab.txt'),
], ids=['no-such-file', 'invalid-protobuf', 'missing-external-data', 'truncated-external-data', 'file-not-found'])
def test_load_failure_from_a_recorded_copy_drops_the_record(monkeypatch, tmp_path, clean_singleton, caplog, error):
    _prime(monkeypatch, tmp_path)

    def broken(*a, **k):
        raise error
    with caplog.at_level(logging.WARNING, logger=pt.logger.name), pytest.raises(ModelLoadError):
        _load(monkeypatch, load_model=broken)
    assert not list(_live(tmp_path).glob('.minuspod-complete*'))
    assert len(_warnings(caplog)) == 1 and 'after the retry interval' in _warnings(caplog)[0]
    hub = _install_hub(monkeypatch, _Hub(write=lambda d: _write_model_files(d, 'int8')))
    assert _load(monkeypatch) == 'ts-model'
    assert [s['local_dir'] for s in hub.snapshots] == [str(_live(tmp_path))]


@pytest.mark.parametrize('value', ['1', 'ON', 'yes', 'True'])
def test_hub_offline_mode_starts_no_check_and_imports_no_hub(monkeypatch, tmp_path, primed, clock, caplog, value):
    monkeypatch.setenv('HF_HUB_OFFLINE', value)
    pending = []
    monkeypatch.setattr(pt, '_recheck_runner', pending.append)
    monkeypatch.setitem(sys.modules, 'huggingface_hub', None)
    with caplog.at_level(logging.INFO, logger=pt.logger.name):
        assert _load(monkeypatch) == 'ts-model'
        clock.now += HOUR
        _load(monkeypatch)
    assert pending == [] and not _warnings(caplog)
    assert len([r for r in caplog.records if 'offline' in r.getMessage()]) == 1


@pytest.mark.parametrize('leftover', ['.staging', '.old'])
def test_leftover_directory_is_cleaned_even_when_no_check_is_due(monkeypatch, tmp_path, clean_singleton, leftover):
    _prime(monkeypatch, tmp_path)
    extra = Path(f'{_live(tmp_path)}{leftover}')
    _write_model_files(extra, 'int8')
    hub = _install_hub(monkeypatch, _Hub())
    _load(monkeypatch)
    assert not extra.exists() and hub.probes == []


def test_download_running_past_the_warn_limit_logs_one_warning(monkeypatch, tmp_path, primed, clock, caplog):
    live = str(_live(tmp_path))

    def slow(d):
        clock.now += pt.PARAKEET_MODEL_DOWNLOAD_WARN_SECONDS
        pt._fetch_model_files(live, 'int8')
        pt._fetch_model_files(live, 'int8')
        _write_model_files(d, 'int8')
    _install_hub(monkeypatch, _Hub(sha=SHA_NEW, write=slow))
    with caplog.at_level(logging.WARNING, logger=pt.logger.name):
        _load(monkeypatch)
    assert len(_warnings(caplog)) == 1 and 'still running' in _warnings(caplog)[0]
    assert _record(_live(tmp_path))['sha'] == SHA_NEW


@pytest.mark.parametrize('error', [
    RuntimeException('[ONNXRuntimeError] : 6 : RUNTIME_EXCEPTION : CUDA failure 2: out of memory'),
    Fail('[ONNXRuntimeError] : 1 : FAIL : CUDA error cudaErrorNoDevice'),
    RuntimeException('[ONNXRuntimeError] : 6 : RUNTIME_EXCEPTION : libcudnn.so.9: cannot open shared '
                     'object file: No such file or directory'),
    MemoryError(),
], ids=['cuda-oom', 'cuda-device', 'missing-cuda-library', 'host-memory'])
def test_transient_load_failure_keeps_the_record(monkeypatch, tmp_path, clean_singleton, caplog, error):
    before = _prime(monkeypatch, tmp_path)

    def broken(*a, **k):
        raise error
    with caplog.at_level(logging.WARNING, logger=pt.logger.name), pytest.raises(ModelLoadError):
        _load(monkeypatch, load_model=broken)
    assert _tree(_live(tmp_path)) == before and _warnings(caplog) == []


def test_leftover_that_cannot_be_removed_is_retried_hourly_with_one_warning(
        monkeypatch, tmp_path, clean_singleton, clock, caplog):
    _prime(monkeypatch, tmp_path)
    live = str(_live(tmp_path))
    Path(f'{live}.old').mkdir()
    attempts = []
    monkeypatch.setattr(pt.shutil, 'rmtree', lambda path, ignore_errors=False: attempts.append(str(path)))
    hub = _install_hub(monkeypatch, _Hub())
    with caplog.at_level(logging.WARNING, logger=pt.logger.name):
        _load(monkeypatch)
        _load(monkeypatch)
    warnings = _warnings(caplog)
    assert len(warnings) == 1 and 'backup' in warnings[0]
    assert f'{pt.PARAKEET_MODEL_NAME}.old' in warnings[0] and str(tmp_path) not in warnings[0]
    assert hub.probes == []
    count = len(attempts)
    clock.now += HOUR
    _load(monkeypatch)
    assert len(attempts) > count
