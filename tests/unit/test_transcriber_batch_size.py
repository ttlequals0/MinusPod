"""Per-device batch-size ceiling for transcription.

BATCH_SIZE_TIERS picks a batch size from audio duration alone, so on a card
where the top tier never fits, every short episode paid two CUDA OOM failures
before settling. The ceiling records the size a downshifted run completed at,
so later episodes start at a size proven to fit. The value is stored with the
device name, since a ceiling for one GPU says nothing about another.
"""

import json

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('batch_size_test_')

import database  # noqa: E402
from transcriber import Transcriber  # noqa: E402

def _db():
    """Resolve the singleton per call, the same way the code under test does.

    A module-level instance captured at import can point at an earlier test
    module's data dir once another module re-bootstraps, so writes here would
    land somewhere the transcriber never reads.
    """
    return database.Database()


def _fresh():
    # Empty string is the "unset" form here: _batch_size_ceiling treats any
    # falsy stored value as no ceiling.
    _db().set_setting(Transcriber.BATCH_CEILING_SETTING, '')
    return Transcriber()


def test_tier_used_when_no_ceiling_stored():
    t = _fresh()
    assert t.get_batch_size_for_duration(30 * 60) == 16


def test_ceiling_clamps_a_higher_tier():
    t = _fresh()
    t.record_batch_size_ceiling(4)
    assert t.get_batch_size_for_duration(30 * 60) == 4


def test_lower_tier_still_wins_over_a_higher_ceiling():
    """A long episode must keep its smaller tier; the ceiling is an upper bound,
    not a floor."""
    t = _fresh()
    t.record_batch_size_ceiling(16)
    assert t.get_batch_size_for_duration(150 * 60) == 4


def test_ceiling_never_drops_below_one():
    t = _fresh()
    t.record_batch_size_ceiling(0)
    assert t.get_batch_size_for_duration(30 * 60) >= 1


def test_ceiling_only_ratchets_down():
    """Evidence that a size does not fit must not be undone by a later write of
    a larger candidate."""
    t = _fresh()
    t.record_batch_size_ceiling(4)
    t.record_batch_size_ceiling(16)
    assert t.get_batch_size_for_duration(30 * 60) == 4


def test_unknown_duration_keeps_the_conservative_default():
    t = _fresh()
    assert t.get_batch_size_for_duration(None) == 8


def test_unknown_duration_is_still_clamped():
    t = _fresh()
    t.record_batch_size_ceiling(2)
    assert t.get_batch_size_for_duration(None) == 2


def test_malformed_stored_ceiling_is_ignored():
    t = _fresh()
    _db().set_setting(Transcriber.BATCH_CEILING_SETTING, 'banana')
    assert t.get_batch_size_for_duration(30 * 60) == 16


def test_ceiling_for_another_device_is_ignored():
    """A ceiling recorded on a different card must not clamp this one."""
    t = _fresh()
    _db().set_setting(
        Transcriber.BATCH_CEILING_SETTING,
        json.dumps({'device': 'some-other-gpu', 'size': 2}))
    assert t.get_batch_size_for_duration(30 * 60) == 16


def test_stored_ceiling_for_this_device_clamps():
    t = _fresh()
    _db().set_setting(
        Transcriber.BATCH_CEILING_SETTING,
        json.dumps({'device': Transcriber._batch_ceiling_device(), 'size': 4}))
    assert t.get_batch_size_for_duration(30 * 60) == 4


def test_legacy_bare_int_applies_to_current_device():
    t = _fresh()
    _db().set_setting(Transcriber.BATCH_CEILING_SETTING, '4')
    assert t.get_batch_size_for_duration(30 * 60) == 4


def test_legacy_value_is_rewritten_with_device_on_next_persist():
    t = _fresh()
    _db().set_setting(Transcriber.BATCH_CEILING_SETTING, '8')
    t.record_batch_size_ceiling(4)
    stored = json.loads(_db().get_setting(Transcriber.BATCH_CEILING_SETTING))
    assert stored == {'device': Transcriber._batch_ceiling_device(), 'size': 4}


def test_persist_ratchets_against_a_legacy_value():
    t = _fresh()
    _db().set_setting(Transcriber.BATCH_CEILING_SETTING, '4')
    t.record_batch_size_ceiling(16)
    stored = json.loads(_db().get_setting(Transcriber.BATCH_CEILING_SETTING))
    assert stored['size'] == 4


def test_persist_replaces_another_devices_ceiling():
    """A different device's ceiling is not evidence here, so it does not ratchet."""
    t = _fresh()
    _db().set_setting(
        Transcriber.BATCH_CEILING_SETTING,
        json.dumps({'device': 'some-other-gpu', 'size': 2}))
    t.record_batch_size_ceiling(8)
    stored = json.loads(_db().get_setting(Transcriber.BATCH_CEILING_SETTING))
    assert stored == {'device': Transcriber._batch_ceiling_device(), 'size': 8}


class _FakePipeline:
    """Raises CUDA OOM for the first `failures` calls, then yields no segments."""

    def __init__(self, failures):
        self.failures = failures
        self.batch_sizes = []

    def transcribe(self, path, **kwargs):
        self.batch_sizes.append(kwargs['batch_size'])
        if len(self.batch_sizes) <= self.failures:
            raise RuntimeError('CUDA out of memory')
        info = type('Info', (), {'language': 'en', 'language_probability': 0.99})()
        return iter([]), info


def _run_transcribe(monkeypatch, failures):
    """Drive Transcriber.transcribe with a fake pipeline; returns (result, pipeline)."""
    import transcriber as transcriber_mod

    pipeline = _FakePipeline(failures)
    monkeypatch.setenv('WHISPER_DEVICE', 'cuda')
    monkeypatch.setattr(transcriber_mod.WhisperModelSingleton,
                        'get_batched_pipeline', classmethod(lambda cls: pipeline))
    monkeypatch.setattr(transcriber_mod.WhisperModelSingleton,
                        'get_current_model_name', classmethod(lambda cls: 'fake'))
    monkeypatch.setattr(transcriber_mod.WhisperModelSingleton,
                        'unload_model', classmethod(lambda cls: None))
    monkeypatch.setattr(Transcriber, 'get_audio_duration', lambda self, p: 30 * 60)
    monkeypatch.setattr(Transcriber, 'preprocess_audio', lambda self, p: None)
    monkeypatch.setattr(Transcriber, 'get_initial_prompt', lambda self, n=None: '')
    monkeypatch.setattr(Transcriber, 'clear_cuda_cache', lambda self: None)

    t = _fresh()
    return t.transcribe('/nonexistent/audio.mp3'), pipeline


def test_completed_downshifted_run_records_the_proven_size(monkeypatch):
    result, pipeline = _run_transcribe(monkeypatch, failures=1)
    assert result == []
    assert pipeline.batch_sizes == [16, 8]
    stored = json.loads(_db().get_setting(Transcriber.BATCH_CEILING_SETTING))
    assert stored == {'device': Transcriber._batch_ceiling_device(), 'size': 8}


def test_run_that_never_completes_records_nothing(monkeypatch):
    """A halved candidate is unproven until a run completes at it, and a
    transient CUDA error must not permanently ratchet the ceiling down."""
    result, _ = _run_transcribe(monkeypatch, failures=99)
    assert result is None
    assert not _db().get_setting(Transcriber.BATCH_CEILING_SETTING)


def test_clean_run_at_the_tier_size_records_nothing(monkeypatch):
    result, pipeline = _run_transcribe(monkeypatch, failures=0)
    assert result == []
    assert pipeline.batch_sizes == [16]
    assert not _db().get_setting(Transcriber.BATCH_CEILING_SETTING)
