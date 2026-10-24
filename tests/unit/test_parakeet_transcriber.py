"""Parakeet transcriber: pure helpers and decode with a stubbed model (#834)."""
import os
import sys
import types
import wave

import numpy as np
import pytest

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
    assert [w['word'] for w in words] == [' one', ' edge', ' two.']
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


def _fake_modules(monkeypatch, load_model, preload=None, snapshot=None):
    calls = []

    def default_snapshot(repo, local_dir=None, allow_patterns=None):
        calls.append({'repo': repo, 'local_dir': local_dir, 'allow_patterns': allow_patterns})
        os.makedirs(local_dir, exist_ok=True)

    hub = types.ModuleType('huggingface_hub')
    hub.snapshot_download = snapshot or default_snapshot
    monkeypatch.setitem(sys.modules, 'huggingface_hub', hub)
    onnx_asr = types.ModuleType('onnx_asr')
    onnx_asr.load_model = load_model
    ort = types.ModuleType('onnxruntime')
    if preload:
        ort.preload_dlls = preload
    monkeypatch.setitem(sys.modules, 'onnx_asr', onnx_asr)
    monkeypatch.setitem(sys.modules, 'onnxruntime', ort)
    return calls


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
    assert words == [' one', ' edge', ' two.']


def test_decode_boundary_dedupe_ignores_case_and_punctuation(tmp_path, monkeypatch):
    path = tmp_path / 'e.wav'
    _write_wav(path, 33.0)
    model = _boundary_model(28.96, 29.04, 'Edge,', 'edge')
    monkeypatch.setattr(pt.ParakeetModelSingleton, 'get_instance', classmethod(lambda cls: model))
    segments = pt.decode(path, 'en')
    words = [w['word'] for s in segments for w in s.get('words', [])]
    assert len(words) == 3
    assert sum(w == ' Edge,' for w in words) == 1


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


def _offline_snapshot(repo, local_dir=None, allow_patterns=None):
    raise OSError('no network')


def test_get_instance_offline_uses_local_copy(monkeypatch, tmp_path, clean_singleton):
    model_dir = tmp_path / 'parakeet' / pt.PARAKEET_MODEL_NAME
    model_dir.mkdir(parents=True)
    (model_dir / 'encoder-model.int8.onnx').write_bytes(b'x')
    loaded = types.SimpleNamespace(with_timestamps=lambda: 'ts-model')
    _fake_modules(monkeypatch, lambda *a, **k: loaded, snapshot=_offline_snapshot)
    monkeypatch.setattr(pt, 'resolve_whisper_device', lambda: 'cpu')
    assert pt.ParakeetModelSingleton.get_instance() == 'ts-model'


def test_get_instance_offline_without_local_copy_fails(monkeypatch, clean_singleton):
    loaded = types.SimpleNamespace(with_timestamps=lambda: 'ts-model')
    _fake_modules(monkeypatch, lambda *a, **k: loaded, snapshot=_offline_snapshot)
    monkeypatch.setattr(pt, 'resolve_whisper_device', lambda: 'cpu')
    with pytest.raises(ModelLoadError, match='no network'):
        pt.ParakeetModelSingleton.get_instance()


def test_words_from_tokens_accepts_sentencepiece_marker_and_skips_specials():
    # A bare marker opens an empty word that is dropped when nothing fills it.
    words = pt.words_from_tokens([MARK + 'Hi', '<unk>', MARK, MARK + 'there', '<|en|>'],
                                 [0.0, 0.1, 0.2, 0.4, 0.5])
    assert [w['word'] for w in words] == ['Hi', 'there']


def test_decode_window_of_space_prefixed_tokens_splits_into_segments(tmp_path, monkeypatch):
    path = tmp_path / 'f.wav'
    _write_wav(path, 30.0)
    toks = [' A', ' b', ' c', ' d', ' e', ' f', ' g.', ' H', ' i', ' j', ' k', ' l.']
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
