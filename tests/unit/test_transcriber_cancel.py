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


class _FakeStatus:
    """Collects update_job_stage_detail calls in place of the file-backed service."""
    calls: list = []
    fail = False

    def update_job_stage_detail(self, slug, episode_id, processed, total, unit, run_id=None):
        if _FakeStatus.fail:
            raise OSError('disk full')
        _FakeStatus.calls.append((slug, episode_id, processed, total, unit, run_id))


@pytest.fixture
def detail_calls(monkeypatch):
    _FakeStatus.calls = []
    _FakeStatus.fail = False
    monkeypatch.setattr(transcriber_mod, 'StatusService', _FakeStatus)
    monkeypatch.setattr(transcriber_mod, '_stage_detail_warned', False)
    return _FakeStatus


def test_stage_detail_throttled_to_interval(monkeypatch, run_ctx, detail_calls):
    now = [100.0]
    monkeypatch.setattr(transcriber_mod.time, 'monotonic', lambda: now[0])
    publish = transcriber_mod._publish_stage_detail
    publish(10, 100, 'seconds')
    now[0] += 4.9
    publish(20, 100, 'seconds')
    now[0] += 0.2
    publish(30, 100, 'seconds')
    publish(40, 100, 'seconds', force=True)
    assert [c[2] for c in detail_calls.calls] == [10, 30, 40]
    assert detail_calls.calls[0] == ('pod', 'ep1', 10, 100, 'seconds', 'run-1')


def test_stage_detail_noop_without_run_context(detail_calls):
    assert run_context.current() is None
    transcriber_mod._publish_stage_detail(10, 100, 'seconds')
    assert detail_calls.calls == []


def test_stage_detail_never_raises_when_write_fails(run_ctx, detail_calls):
    detail_calls.fail = True
    transcriber_mod._publish_stage_detail(10, 100, 'seconds', force=True)
    transcriber_mod._publish_stage_detail(20, 100, 'seconds', force=True)


def test_stage_detail_skips_zero_total_and_clamps(run_ctx, detail_calls):
    transcriber_mod._publish_stage_detail(5, 0, 'seconds', force=True)
    transcriber_mod._publish_stage_detail(5, None, 'seconds', force=True)
    transcriber_mod._publish_stage_detail(150, 100, 'seconds', force=True)
    assert [(c[2], c[3]) for c in detail_calls.calls] == [(100, 100)]


def test_single_pass_publishes_seconds(monkeypatch, run_ctx, detail_calls):
    _patch_local(monkeypatch, _LazyPipeline(total=3))
    _advance_clock_per_call(monkeypatch, step=10.0)

    Transcriber().transcribe('/nonexistent/audio.wav')

    assert detail_calls.calls
    assert all(c[3] == 30 * 60 and c[4] == 'seconds' for c in detail_calls.calls)
    assert detail_calls.calls[-1][2] == 30.0


def test_chunk_window_publishes_episode_absolute_seconds(monkeypatch, run_ctx, detail_calls):
    _patch_local(monkeypatch, _LazyPipeline(total=2))
    _advance_clock_per_call(monkeypatch, step=10.0)
    token = transcriber_mod._progress_window.set((600.0, 3000.0))
    try:
        Transcriber().transcribe('/nonexistent/audio.wav')
    finally:
        transcriber_mod._progress_window.reset(token)

    assert [(c[2], c[3]) for c in detail_calls.calls] == [(610.0, 3000.0), (620.0, 3000.0)]


def test_chunked_local_publishes_chunk_end(monkeypatch, run_ctx, detail_calls):
    monkeypatch.setenv('WHISPER_DEVICE', 'cpu')
    single = transcriber_mod.local.WhisperModelSingleton
    monkeypatch.setattr(single, 'get_configured_model', classmethod(lambda cls: 'fake'))
    monkeypatch.setattr(single, 'get_batched_pipeline', classmethod(lambda cls: None))
    monkeypatch.setattr(transcriber_mod.chunking, 'calculate_optimal_chunk_duration',
                        lambda model, device: (100, 'test'))
    monkeypatch.setattr(transcriber_mod, 'CHUNK_OVERLAP_SECONDS', 0)

    class _Prefetcher:
        def __init__(self, path):
            pass

        def prime(self, *a):
            pass

        def take(self, *a):
            return "/nonexistent/chunk.wav"

        def close(self):
            pass

    monkeypatch.setattr(transcriber_mod, '_ChunkPrefetcher', _Prefetcher)
    monkeypatch.setattr(transcriber_mod, 'clear_gpu_memory', lambda: None)
    seen = []

    def fake_transcribe(self, path, **kw):
        seen.append(transcriber_mod._progress_window.get())
        return [{'start': 0.0, 'end': 1.0, 'text': 'x', 'words': []}]

    monkeypatch.setattr(Transcriber, 'transcribe', fake_transcribe)
    Transcriber()._transcribe_chunked_local_body(
        '/nonexistent/audio.wav', 250.0, {'backend': 'local'})

    assert seen == [(0, 250.0), (100, 250.0), (200, 250.0)]
    assert [(c[2], c[3], c[4]) for c in detail_calls.calls] == [
        (100, 250.0, 'seconds'), (200, 250.0, 'seconds'), (250.0, 250.0, 'seconds')]
    assert transcriber_mod._progress_window.get() is None


def test_api_chunk_plan_publishes_completed_chunks(monkeypatch, run_ctx, detail_calls):
    monkeypatch.setattr(transcriber_mod.chunking, 'extract_audio_chunk',
                        lambda *a, **kw: '/nonexistent/chunk.wav')
    monkeypatch.setattr(Transcriber, '_transcribe_via_api',
                        lambda self, path, ws, **kw: [{'start': 0.0, 'end': 1.0, 'text': 'x'}])
    ctx = transcriber_mod._ChunkPlanContext(
        audio_path='/nonexistent/audio.wav', language_override=None, prefix='',
        max_workers=1, max_failed_chunks=2)
    plan = [(0, 0.0, 100.0), (1, 100.0, 200.0), (2, 200.0, 250.0)]
    results = [None] * 3

    Transcriber()._run_chunk_plan(ctx, plan, {'backend': 'openai-api'}, results, False)

    assert [(c[2], c[3], c[4]) for c in detail_calls.calls] == [
        (1, 3, 'chunks'), (2, 3, 'chunks'), (3, 3, 'chunks')]
