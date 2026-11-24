"""A Cancel click must stop a local transcription instead of waiting for it to finish.

Before this, nothing inside Transcriber checked the run's cancel flag: a cancel that
arrived during download/preparation still started a full decode, and one that arrived
during the decode was honoured only after the whole episode was transcribed (minutes
on CPU). The outer `except Exception` also turned a cancel into a `None` result.
"""

import pytest

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('transcriber_cancel_test_')

import run_context  # noqa: E402
import transcriber as transcriber_mod  # noqa: E402
from cancel import ProcessingCancelled, ProcessingOwnershipLost  # noqa: E402
from transcriber import Transcriber  # noqa: E402


class _Segment:
    def __init__(self, i):
        self.start = i * 10.0
        self.end = i * 10.0 + 10.0
        self.text = f'This is sentence number {i} of the episode.'
        self.words = None


class _LazyPipeline:
    """Yields segments one by one and records how many were decoded."""

    def __init__(self, total):
        self.total = total
        self.decoded = 0
        self.calls = 0

    def transcribe(self, path, **kwargs):
        self.calls += 1
        info = type('Info', (), {'language': 'en', 'language_probability': 0.99})()

        def gen():
            for i in range(self.total):
                self.decoded += 1
                yield _Segment(i)
        return gen(), info


def _advance_clock_per_call(monkeypatch, step):
    """time.monotonic moves `step` seconds on every call."""
    now = [1000.0]

    def fake():
        now[0] += step
        return now[0]
    monkeypatch.setattr(transcriber_mod.time, 'monotonic', fake)


def _patch_local(monkeypatch, pipeline):
    monkeypatch.setenv('WHISPER_DEVICE', 'cpu')
    single = transcriber_mod.local.WhisperModelSingleton
    monkeypatch.setattr(single, 'get_batched_pipeline', classmethod(lambda cls: pipeline))
    monkeypatch.setattr(single, 'get_current_model_name', classmethod(lambda cls: 'fake-model'))
    monkeypatch.setattr(single, 'unload_model', classmethod(lambda cls: None))
    monkeypatch.setattr(Transcriber, 'get_audio_duration', lambda self, p: 30 * 60)
    monkeypatch.setattr(Transcriber, 'preprocess_audio', lambda self, p: None)


@pytest.fixture
def run_ctx():
    """A run whose cancel check raises once `cancelled_after` checks have passed."""
    ctx = run_context.begin('pod', 'ep1', run_id='run-1')
    state = {'checks': 0, 'cancelled_after': None}

    def check():
        state['checks'] += 1
        if state['cancelled_after'] is not None and state['checks'] > state['cancelled_after']:
            raise ProcessingCancelled()

    ctx.llm_cancel_check = check
    ctx.llm_cancel_exceptions = (ProcessingCancelled,)
    yield state
    run_context.end(ctx)


def test_cancel_before_decode_never_starts_whisper(monkeypatch, run_ctx):
    pipeline = _LazyPipeline(total=50)
    _patch_local(monkeypatch, pipeline)
    run_ctx['cancelled_after'] = 0

    with pytest.raises(ProcessingCancelled):
        Transcriber().transcribe('/nonexistent/audio.wav')

    assert pipeline.calls == 0
    assert pipeline.decoded == 0


def test_cancel_during_decode_stops_between_segments(monkeypatch, run_ctx):
    pipeline = _LazyPipeline(total=50)
    _patch_local(monkeypatch, pipeline)
    # Entry check passes, then the first in-loop check passes, the second raises.
    run_ctx['cancelled_after'] = 2
    _advance_clock_per_call(monkeypatch, step=transcriber_mod.CANCEL_CHECK_INTERVAL_SECONDS)

    t = Transcriber()
    with pytest.raises(ProcessingCancelled):
        t.transcribe('/nonexistent/audio.wav')

    assert pipeline.calls == 1
    assert 0 < pipeline.decoded < pipeline.total
    # A cancel is not a transcription failure.
    assert (t.last_transcription_stats or {}).get('outcome') != 'failed'


def test_cancel_check_is_throttled_by_time(monkeypatch, run_ctx):
    pipeline = _LazyPipeline(total=40)
    _patch_local(monkeypatch, pipeline)
    # The clock moves 0.5 s per call, so a check is due on every fourth segment.
    _advance_clock_per_call(monkeypatch, step=0.5)

    result = Transcriber().transcribe('/nonexistent/audio.wav')

    assert len(result) == 40
    assert 2 <= run_ctx['checks'] < 40 // 2


def test_without_cancel_transcription_completes(monkeypatch, run_ctx):
    pipeline = _LazyPipeline(total=12)
    _patch_local(monkeypatch, pipeline)

    result = Transcriber().transcribe('/nonexistent/audio.wav')

    assert len(result) == 12
    assert run_ctx['checks'] >= 2  # entry check plus at least one in-loop check


def test_no_run_context_is_unaffected(monkeypatch):
    pipeline = _LazyPipeline(total=7)
    _patch_local(monkeypatch, pipeline)
    assert run_context.current() is None

    result = Transcriber().transcribe('/nonexistent/audio.wav')

    assert len(result) == 7


@pytest.mark.parametrize('exc_type', [ProcessingCancelled, ProcessingOwnershipLost])
@pytest.mark.parametrize('chunked', [False, True])
def test_cancel_in_chunked_local_run_propagates_without_oom_handling(monkeypatch, run_ctx, exc_type, chunked):
    unloads = []
    monkeypatch.setattr(transcriber_mod.local, '_unload_local_models', lambda: unloads.append(1))
    monkeypatch.setattr(transcriber_mod, 'resolve_whisper_device', lambda: 'cpu')
    monkeypatch.setattr(transcriber_mod.local.WhisperModelSingleton, 'get_configured_model',
                        classmethod(lambda cls: 'fake-model'))
    chunk = (60.0 if chunked else 3600.0, 'test')
    monkeypatch.setattr(transcriber_mod.chunking, 'calculate_optimal_chunk_duration', lambda m, d: chunk)
    monkeypatch.setattr(transcriber_mod.local.WhisperModelSingleton, 'get_batched_pipeline',
                        classmethod(lambda cls: None))
    run_ctx_obj = run_context.current()
    run_ctx_obj.llm_cancel_exceptions = (ProcessingCancelled, ProcessingOwnershipLost)

    class FakePrefetcher:
        def __init__(self, path):
            pass

        def prime(self, *a):
            pass

        def take(self, *a):
            return '/nonexistent/chunk.wav'

        def close(self):
            pass

    monkeypatch.setattr(transcriber_mod, '_ChunkPrefetcher', FakePrefetcher)
    calls = []

    def boom(self, *a, **k):
        calls.append(1)
        raise exc_type('episode in feed room-cuda was cancelled (oom)')

    monkeypatch.setattr(Transcriber, 'transcribe', boom)

    with pytest.raises(exc_type):
        Transcriber()._transcribe_chunked_local_body(
            '/nonexistent/audio.wav', 600.0, transcriber_mod.settings.active_whisper_settings())

    assert calls == [1]
    assert unloads == []
