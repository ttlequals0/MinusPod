"""Transcriber failover (#806)."""
from unittest.mock import MagicMock, patch

import pytest

from tests.app_bootstrap import bootstrap
bootstrap('transcriber_failover_test_')

import failover
import transcriber
from transcriber import ServiceUnavailableError, Transcriber
from utils.errors import LocalTranscriptionUnavailableError

ACTIVE = {'backend': 'openai-api', 'api_base_url': 'http://a.example.com/v1', 'api_key': 'k1',
          'api_model': 'whisper-1', 'language': 'en', 'skip_flac_compression': True,
          'api_timeout': 60, 'max_attempts': 1}
FAILOVER = {**ACTIVE, 'api_base_url': 'http://b.example.com/v1', 'api_key': 'k2', 'is_failover': True}

_CHUNK_SETTINGS = {'max_chunk_seconds': 100, 'concurrent_chunks': 1, 'chunk_overlap_seconds': 10}


@pytest.fixture
def t():
    tr = Transcriber.__new__(Transcriber)
    tr.last_transcription_stats = {}
    return tr


def _seg(start, end):
    return [{'start': start, 'end': end, 'text': 'x', 'words': []}]


def _chunk_path_for(start):
    """Unambiguous per-chunk extraction path, keyed on the chunk's own start
    time so no chunk's path is ever a substring of another's."""
    return f'/tmp/chunk_start_{start:.1f}_.flac'


def test_active_failover_uses_failover_settings_up_front(t):
    with patch.object(failover, 'is_active', return_value=True), \
            patch.object(transcriber, '_get_failover_whisper_settings', return_value=FAILOVER), \
            patch.object(t, 'get_audio_duration', return_value=100.0), \
            patch.object(t, '_transcribe_chunked_parallel_api', return_value=_seg(0, 1)) as api:
        t.transcribe_chunked('/tmp/a.mp3')
    assert api.call_args.args[2]['api_base_url'] == 'http://b.example.com/v1'


def test_chunk_outage_reruns_failed_chunks_on_failover(t):
    calls = []

    def fake_via_api(path, settings, **kw):
        calls.append(settings['api_base_url'])
        if settings['api_base_url'].startswith('http://a.'):
            if path == _chunk_path_for(100.0):
                return _seg(0, 1)
            raise ServiceUnavailableError('whisper', 'down')
        return _seg(0, 1)

    with patch.object(failover, 'is_active', return_value=False), \
            patch.object(failover, 'is_configured', return_value=True), \
            patch.object(failover, 'trigger', return_value=True) as trig, \
            patch.object(transcriber, '_get_failover_whisper_settings', return_value=FAILOVER), \
            patch.object(transcriber, '_get_chunk_settings', return_value=_CHUNK_SETTINGS), \
            patch.object(transcriber, 'extract_audio_chunk',
                        side_effect=lambda path, start, end, **k: _chunk_path_for(start)), \
            patch.object(transcriber, '_unlink_quiet'), \
            patch.object(t, '_transcribe_via_api', side_effect=fake_via_api), \
            patch.object(t, 'filter_hallucinations', side_effect=lambda s: s):
        segs = t._transcribe_chunked_parallel_api('/tmp/a.mp3', 300.0, ACTIVE)
    assert segs
    trig.assert_called_once()
    assert any(u.startswith('http://b.') for u in calls)


def test_same_backend_switch_keeps_already_finished_chunk_results(t):
    """Chunk 0 succeeds on the primary before chunk 1's outage is seen;
    its result must not be re-fetched on the failover pass."""
    calls = []

    def fake_via_api(path, settings, **kw):
        calls.append((path, settings['api_base_url']))
        if settings['api_base_url'].startswith('http://a.'):
            if path == _chunk_path_for(0.0):
                return _seg(0, 1)
            raise ServiceUnavailableError('whisper', 'down')
        return _seg(0, 1)

    with patch.object(failover, 'is_active', return_value=False), \
            patch.object(failover, 'is_configured', return_value=True), \
            patch.object(failover, 'trigger', return_value=True), \
            patch.object(transcriber, '_get_failover_whisper_settings', return_value=FAILOVER), \
            patch.object(transcriber, '_get_chunk_settings', return_value=_CHUNK_SETTINGS), \
            patch.object(transcriber, 'extract_audio_chunk',
                        side_effect=lambda path, start, end, **k: _chunk_path_for(start)), \
            patch.object(transcriber, '_unlink_quiet'), \
            patch.object(t, '_transcribe_via_api', side_effect=fake_via_api), \
            patch.object(t, 'filter_hallucinations', side_effect=lambda s: s):
        segs = t._transcribe_chunked_parallel_api('/tmp/a.mp3', 300.0, ACTIVE)
    assert segs
    assert [c for c in calls if c[0] == _chunk_path_for(0.0)] == [
        (_chunk_path_for(0.0), 'http://a.example.com/v1')]


def test_outage_without_failover_configured_propagates(t):
    with patch.object(failover, 'is_active', return_value=False), \
            patch.object(failover, 'is_configured', return_value=False), \
            patch.object(transcriber, '_get_chunk_settings', return_value=_CHUNK_SETTINGS), \
            patch.object(transcriber, 'extract_audio_chunk', return_value='/tmp/c.flac'), \
            patch.object(transcriber, '_unlink_quiet'), \
            patch.object(t, '_transcribe_via_api', side_effect=ServiceUnavailableError('whisper', 'down')):
        with pytest.raises(ServiceUnavailableError):
            t._transcribe_chunked_parallel_api('/tmp/a.mp3', 300.0, ACTIVE)


def test_single_shot_outage_switches_to_failover_api(t):
    """A short episode (duration below the chunk size) never reaches the
    chunk plan, so the trigger handling has to wrap the single-shot call
    directly (#806 review)."""
    calls = []

    def fake_transcribe(audio_path, language_override=None, whisper_settings=None, **kw):
        calls.append(whisper_settings['api_base_url'])
        if whisper_settings['api_base_url'].startswith('http://a.'):
            raise ServiceUnavailableError('whisper', 'down')
        return _seg(0, 1)

    with patch.object(failover, 'is_configured', return_value=True), \
            patch.object(failover, 'trigger', return_value=True) as trig, \
            patch.object(transcriber, '_get_failover_whisper_settings', return_value=FAILOVER), \
            patch.object(transcriber, '_get_chunk_settings', return_value=_CHUNK_SETTINGS), \
            patch.object(t, 'transcribe', side_effect=fake_transcribe):
        segs = t._transcribe_chunked_parallel_api('/tmp/a.mp3', 50.0, ACTIVE)
    assert segs
    trig.assert_called_once()
    assert any(u.startswith('http://b.') for u in calls)


def test_single_shot_outage_without_failover_configured_propagates(t):
    with patch.object(failover, 'is_configured', return_value=False), \
            patch.object(transcriber, '_get_chunk_settings', return_value=_CHUNK_SETTINGS), \
            patch.object(t, 'transcribe', side_effect=ServiceUnavailableError('whisper', 'down')):
        with pytest.raises(ServiceUnavailableError):
            t._transcribe_chunked_parallel_api('/tmp/a.mp3', 50.0, ACTIVE)


def test_backend_type_switch_reruns_whole_episode_locally(t):
    local_fo = {**FAILOVER, 'backend': 'local', 'local_model': 'base'}
    with patch.object(failover, 'is_active', return_value=False), \
            patch.object(failover, 'is_configured', return_value=True), \
            patch.object(failover, 'trigger', return_value=True), \
            patch.object(transcriber, '_get_failover_whisper_settings', return_value=local_fo), \
            patch.object(transcriber, '_get_chunk_settings', return_value=_CHUNK_SETTINGS), \
            patch.object(transcriber, 'extract_audio_chunk', return_value='/tmp/c.flac'), \
            patch.object(transcriber, '_unlink_quiet'), \
            patch.object(t, '_transcribe_via_api', side_effect=ServiceUnavailableError('whisper', 'down')), \
            patch.object(t, '_transcribe_chunked_local', return_value=_seg(0, 1)) as local:
        segs = t._transcribe_chunked_parallel_api('/tmp/a.mp3', 300.0, ACTIVE)
    assert segs and local.called
    assert transcriber._local_model_override.get() is None  # reset after the run


def test_local_chunked_switches_backend_on_trigger_error(t):
    """A local-backend outage (missing stack) fails over to the API backend."""
    local_settings = {'backend': 'local', 'language': 'en', 'is_failover': False}
    with patch.object(failover, 'is_configured', return_value=True), \
            patch.object(failover, 'trigger', return_value=True) as trig, \
            patch.object(transcriber, '_get_failover_whisper_settings', return_value=FAILOVER), \
            patch.object(transcriber, '_require_local_transcription',
                        side_effect=LocalTranscriptionUnavailableError('no local stack')), \
            patch.object(t, 'get_audio_duration', return_value=300.0), \
            patch.object(t, '_transcribe_chunked_parallel_api', return_value=_seg(0, 1)) as api:
        segs = t._transcribe_chunked_local('/tmp/a.mp3', 300.0, local_settings, None)
    assert segs
    trig.assert_called_once()
    assert api.call_args.args[2]['api_base_url'] == 'http://b.example.com/v1'


def test_local_chunked_does_not_switch_when_already_on_failover(t):
    """Already on the failover config: a further trigger error must not loop."""
    failover_local_settings = {'backend': 'local', 'language': 'en', 'is_failover': True}
    with patch.object(failover, 'is_configured', return_value=True), \
            patch.object(failover, 'trigger') as trig, \
            patch.object(transcriber, '_require_local_transcription',
                        side_effect=LocalTranscriptionUnavailableError('no local stack')):
        with pytest.raises(LocalTranscriptionUnavailableError):
            t._transcribe_chunked_local('/tmp/a.mp3', 300.0, failover_local_settings, None)
    trig.assert_not_called()


def test_failover_settings_language_inherits_active():
    db = MagicMock()
    values = {'failover_whisper_backend': 'openai-api', 'failover_whisper_api_base_url': 'http://b.example.com/v1',
              'failover_whisper_api_model': 'whisper-1', 'failover_whisper_language': '',
              'failover_whisper_api_timeout_seconds': '120', 'whisper_language': 'de',
              'whisper_max_attempts': '2'}
    db.get_setting.side_effect = values.get
    db.get_secret.return_value = 'k2'
    db.get_setting_float.side_effect = lambda k, d: float(values.get(k, d))
    with patch('database.Database', return_value=db):
        s = transcriber._get_failover_whisper_settings()
    assert s['language'] == 'de' and s['api_key'] == 'k2' and s['is_failover'] is True


def test_single_shot_trigger_failure_reraises_original_error(t):
    original = ServiceUnavailableError('whisper', 'down')
    with patch.object(failover, 'is_configured', return_value=True), \
            patch.object(failover, '_write_state', side_effect=RuntimeError('db locked')), \
            patch.object(transcriber, '_get_chunk_settings', return_value=_CHUNK_SETTINGS), \
            patch.object(t, 'transcribe', side_effect=original):
        with pytest.raises(ServiceUnavailableError) as exc:
            t._transcribe_chunked_parallel_api('/tmp/a.mp3', 50.0, ACTIVE)
    assert exc.value is original


def test_second_pass_classifies_on_its_own_errors(t):
    """First-pass outages must not make a non-connectivity second-pass failure look like one."""
    def fake_via_api(path, settings, **kw):
        if settings['api_base_url'].startswith('http://a.'):
            raise ServiceUnavailableError('whisper', 'down')
        raise ValueError('bad audio')

    seen = []
    real = Transcriber._run_chunk_plan

    def spy(*args, **kw):
        out = real(*args, **kw)
        seen.append(list(out[1]))
        return out

    with patch.object(failover, 'is_active', return_value=False), \
            patch.object(failover, 'is_configured', return_value=True), \
            patch.object(failover, 'trigger', return_value=True), \
            patch.object(transcriber, '_get_failover_whisper_settings', return_value=FAILOVER), \
            patch.object(transcriber, '_get_chunk_settings', return_value=_CHUNK_SETTINGS), \
            patch.object(transcriber, 'extract_audio_chunk',
                        side_effect=lambda path, start, end, **k: _chunk_path_for(start)), \
            patch.object(transcriber, '_unlink_quiet'), \
            patch.object(Transcriber, '_run_chunk_plan', spy), \
            patch.object(t, '_transcribe_via_api', side_effect=fake_via_api):
        assert t._transcribe_chunked_parallel_api('/tmp/a.mp3', 300.0, ACTIVE) is None
    assert seen[1] == []


def test_run_context_records_failover_use(t):
    import run_context
    ctx = run_context.begin('example-podcast', 'a1b2c3d4e5f6', run_id='r-wfo')
    try:
        with patch.object(t, '_transcribe_via_api', return_value=_seg(0, 1)):
            t.transcribe('/tmp/a.mp3', whisper_settings=ACTIVE)
            assert ctx.whisper_failover_used is False
            t.transcribe('/tmp/a.mp3', whisper_settings=FAILOVER)
        assert ctx.whisper_failover_used is True
    finally:
        run_context.end(ctx)


def test_local_failover_model_override_covers_chunk_sizing(t):
    local_fo = {'backend': 'local', 'language': 'en', 'is_failover': True, 'local_model': 'base'}
    seen = []

    def body(*a, **k):
        seen.append(transcriber.WhisperModelSingleton.get_configured_model())
        return _seg(0, 1)

    with patch.object(transcriber, '_require_local_transcription'), \
            patch.object(t, '_transcribe_chunked_local_body', side_effect=body):
        t._transcribe_chunked_local('/tmp/a.mp3', 300.0, local_fo, None)
    assert seen == ['base']
    assert transcriber._local_model_override.get() is None
