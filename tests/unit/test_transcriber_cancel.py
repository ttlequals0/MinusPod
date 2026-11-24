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
from cancel import ProcessingCancelled  # noqa: E402
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

    t = Transcriber()
    with pytest.raises(ProcessingCancelled):
        t.transcribe('/nonexistent/audio.wav')

    assert pipeline.calls == 1
    assert 0 < pipeline.decoded < pipeline.total
    # A cancel is not a transcription failure.
    assert (t.last_transcription_stats or {}).get('outcome') != 'failed'


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
