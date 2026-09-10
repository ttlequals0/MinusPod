"""Per-device batch-size ceiling for transcription.

BATCH_SIZE_TIERS picks a batch size from audio duration alone, so on a card
where the top tier never fits, every short episode paid two CUDA OOM failures
before settling. The ceiling records the size a downshifted run completed at,
so later episodes start at a size proven to fit. The value is stored with the
device name, since a ceiling for one GPU says nothing about another. After
BATCH_CEILING_TTL_DAYS the next run probes one size up, so a transient OOM
cannot pin every later episode at a smaller size for good, while a genuine
ceiling costs one OOM retry per expiry and is then re-recorded.
"""

import json
from datetime import datetime, timedelta, timezone

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('batch_size_test_')

import database  # noqa: E402
from transcriber import Transcriber  # noqa: E402
from utils.time import parse_iso_utc  # noqa: E402

def _db():
    """Resolve the singleton per call, the same way the code under test does.

    A module-level instance captured at import can point at an earlier test
    module's data dir once another module re-bootstraps, so writes here would
    land somewhere the transcriber never reads.
    """
    return database.Database()


def _payload(size, age_days=0, device=None):
    recorded = datetime.now(timezone.utc) - timedelta(days=age_days)
    return json.dumps({'device': device or Transcriber._batch_ceiling_device(),
                       'size': size, 'recorded_at': recorded.isoformat()})


def _stored():
    return json.loads(_db().get_setting(Transcriber.BATCH_CEILING_SETTING))


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
    _db().set_setting(Transcriber.BATCH_CEILING_SETTING, _payload(2, device='some-other-gpu'))
    assert t.get_batch_size_for_duration(30 * 60) == 16


def test_stored_ceiling_for_this_device_clamps():
    t = _fresh()
    _db().set_setting(Transcriber.BATCH_CEILING_SETTING, _payload(4))
    assert t.get_batch_size_for_duration(30 * 60) == 4


def test_expired_ceiling_probes_one_size_up():
    t = _fresh()
    _db().set_setting(
        Transcriber.BATCH_CEILING_SETTING,
        _payload(4, age_days=Transcriber.BATCH_CEILING_TTL_DAYS + 1))
    assert t.get_batch_size_for_duration(30 * 60) == 8


def test_expired_probe_never_exceeds_the_tier():
    t = _fresh()
    _db().set_setting(
        Transcriber.BATCH_CEILING_SETTING,
        _payload(12, age_days=Transcriber.BATCH_CEILING_TTL_DAYS + 1))
    assert t.get_batch_size_for_duration(30 * 60) == 16


def test_ceiling_without_a_timestamp_reads_as_expired():
    """Pre-2.96.0 payloads carry no recorded_at; the upgrade probes up from them."""
    t = _fresh()
    _db().set_setting(
        Transcriber.BATCH_CEILING_SETTING,
        json.dumps({'device': Transcriber._batch_ceiling_device(), 'size': 1}))
    assert t.get_batch_size_for_duration(30 * 60) == 2


def test_naive_timestamp_is_read_as_utc():
    t = _fresh()
    naive = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    _db().set_setting(
        Transcriber.BATCH_CEILING_SETTING,
        json.dumps({'device': Transcriber._batch_ceiling_device(), 'size': 4,
                    'recorded_at': naive}))
    assert t.get_batch_size_for_duration(30 * 60) == 4


def test_legacy_bare_int_is_ignored():
    t = _fresh()
    _db().set_setting(Transcriber.BATCH_CEILING_SETTING, '4')
    assert t.get_batch_size_for_duration(30 * 60) == 16


def test_persist_writes_device_size_and_timestamp():
    t = _fresh()
    before = datetime.now(timezone.utc).replace(microsecond=0)
    t.record_batch_size_ceiling(4)
    stored = _stored()
    assert stored['device'] == Transcriber._batch_ceiling_device()
    assert stored['size'] == 4
    assert parse_iso_utc(stored['recorded_at']) >= before


def test_recording_an_unchanged_ceiling_keeps_its_timestamp():
    """A ceiling in use must still age out, or a bogus one would never expire."""
    t = _fresh()
    _db().set_setting(Transcriber.BATCH_CEILING_SETTING, _payload(4, age_days=1))
    recorded_at = _stored()['recorded_at']
    t.record_batch_size_ceiling(4)
    assert _stored()['recorded_at'] == recorded_at


def test_persist_after_a_successful_probe_records_the_larger_size():
    t = _fresh()
    _db().set_setting(
        Transcriber.BATCH_CEILING_SETTING,
        _payload(4, age_days=Transcriber.BATCH_CEILING_TTL_DAYS + 1))
    t.record_batch_size_ceiling(8)
    assert _stored()['size'] == 8


def test_persist_does_not_ratchet_against_an_expired_value():
    t = _fresh()
    _db().set_setting(
        Transcriber.BATCH_CEILING_SETTING,
        _payload(2, age_days=Transcriber.BATCH_CEILING_TTL_DAYS + 1))
    t.record_batch_size_ceiling(8)
    assert _stored()['size'] == 8


def test_persist_replaces_another_devices_ceiling():
    """A different device's ceiling is not evidence here, so it does not ratchet."""
    t = _fresh()
    _db().set_setting(Transcriber.BATCH_CEILING_SETTING, _payload(2, device='some-other-gpu'))
    t.record_batch_size_ceiling(8)
    assert _stored()['size'] == 8


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


def _run_transcribe(monkeypatch, failures, error='CUDA out of memory', ceiling=None):
    """Drive Transcriber.transcribe with a fake pipeline; returns (result, pipeline)."""
    import transcriber as transcriber_mod

    pipeline = _FakePipeline(failures, error)
    monkeypatch.setenv('WHISPER_DEVICE', 'cuda')
    monkeypatch.setattr(transcriber_mod.WhisperModelSingleton,
                        'get_batched_pipeline', classmethod(lambda cls: pipeline))
    monkeypatch.setattr(transcriber_mod.WhisperModelSingleton,
                        'get_current_model_name', classmethod(lambda cls: 'fake'))
    monkeypatch.setattr(transcriber_mod.WhisperModelSingleton,
                        'unload_model', classmethod(lambda cls: None))
    monkeypatch.setattr(Transcriber, 'get_audio_duration', lambda self, p: 30 * 60)
    monkeypatch.setattr(Transcriber, 'preprocess_audio', lambda self, p: None)
    monkeypatch.setattr(Transcriber, 'clear_cuda_cache', lambda self: None)

    t = _fresh()
    if ceiling:
        _db().set_setting(Transcriber.BATCH_CEILING_SETTING, ceiling)
    return t.transcribe('/nonexistent/audio.mp3'), pipeline


def test_completed_downshifted_run_records_the_proven_size(monkeypatch):
    result, pipeline = _run_transcribe(monkeypatch, failures=1)
    assert result == []
    assert pipeline.batch_sizes == [16, 8]
    assert _stored()['size'] == 8


def test_run_that_never_completes_records_nothing(monkeypatch):
    """A halved candidate is unproven until a run completes at it, and a
    transient CUDA error must not permanently ratchet the ceiling down."""
    result, _ = _run_transcribe(monkeypatch, failures=99)
    assert result is None
    assert not _db().get_setting(Transcriber.BATCH_CEILING_SETTING)


def test_non_oom_cuda_error_retries_at_the_same_size(monkeypatch):
    """Only an actual OOM says the size does not fit; other CUDA errors are
    retried as-is so they cannot record a bogus ceiling."""
    result, pipeline = _run_transcribe(
        monkeypatch, failures=1, error='CUDA error: an illegal memory access was encountered')
    assert result == []
    assert pipeline.batch_sizes == [16, 16]
    assert not _db().get_setting(Transcriber.BATCH_CEILING_SETTING)


def test_run_at_an_applied_ceiling_re_records_it(monkeypatch):
    payload = _payload(4, age_days=1)
    result, pipeline = _run_transcribe(monkeypatch, failures=0, ceiling=payload)
    assert result == []
    assert pipeline.batch_sizes == [4]
    assert _stored() == json.loads(payload)


def test_failed_probe_completes_at_the_old_ceiling_with_a_fresh_stamp(monkeypatch):
    old = _payload(4, age_days=Transcriber.BATCH_CEILING_TTL_DAYS + 1)
    result, pipeline = _run_transcribe(monkeypatch, failures=1, ceiling=old)
    assert result == []
    assert pipeline.batch_sizes == [8, 4]
    stored = _stored()
    assert stored['size'] == 4
    assert stored['recorded_at'] != json.loads(old)['recorded_at']


def test_clean_run_at_the_tier_size_records_nothing(monkeypatch):
    result, pipeline = _run_transcribe(monkeypatch, failures=0)
    assert result == []
    assert pipeline.batch_sizes == [16]
    assert not _db().get_setting(Transcriber.BATCH_CEILING_SETTING)
