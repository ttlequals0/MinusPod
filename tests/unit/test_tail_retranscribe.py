"""Tail re-transcription tunables and the transcriber vad_filter parameter
(spec 1.2). Whisper's VAD drops quiet DAI post-rolls; the tail path needs a
transcribe() call with vad_filter=False."""
from unittest.mock import MagicMock, patch

from config import (
    TAIL_RETRANSCRIBE_MIN_SECONDS,
    TAIL_RETRANSCRIBE_MAX_SECONDS,
    resolve_tail_retranscribe_tunables,
)
from transcriber import Transcriber


def test_tail_tunables_defaults():
    assert TAIL_RETRANSCRIBE_MIN_SECONDS == 10.0
    assert TAIL_RETRANSCRIBE_MAX_SECONDS == 600.0
    assert resolve_tail_retranscribe_tunables(None) == {
        'min_seconds': 10.0, 'max_seconds': 600.0}


def test_tail_tunables_read_db_overrides():
    db = MagicMock()
    db.get_setting_float.side_effect = lambda key, default: {
        'tail_retranscribe_min_seconds': 20.0,
        'tail_retranscribe_max_seconds': 300.0,
    }[key]
    assert resolve_tail_retranscribe_tunables(db) == {
        'min_seconds': 20.0, 'max_seconds': 300.0}


def test_tail_tunables_survive_db_failure():
    db = MagicMock()
    db.get_setting_float.side_effect = RuntimeError('locked')
    assert resolve_tail_retranscribe_tunables(db) == {
        'min_seconds': 10.0, 'max_seconds': 600.0}


def _local_settings():
    return {'backend': 'local', 'api_base_url': '', 'api_key': '',
            'api_model': 'whisper-1', 'language': 'en',
            'skip_flac_compression': False}


def _mock_model():
    model = MagicMock()
    info = MagicMock()
    info.language = 'en'
    info.language_probability = 0.99
    model.transcribe.return_value = (iter([]), info)
    return model


def _transcribe_with(vad_kwargs):
    t = Transcriber()
    model = _mock_model()
    with patch('transcriber._get_whisper_settings',
               return_value=_local_settings()), \
         patch('transcriber.WhisperModelSingleton') as singleton, \
         patch.object(Transcriber, 'preprocess_audio', return_value=None), \
         patch.object(Transcriber, 'get_audio_duration', return_value=42.0):
        singleton.get_batched_pipeline.return_value = model
        singleton.get_current_model_name.return_value = 'small'
        t.transcribe('/nonexistent.wav', **vad_kwargs)
    return model.transcribe.call_args.kwargs


def test_transcribe_default_keeps_vad_filter_on():
    kwargs = _transcribe_with({})
    assert kwargs['vad_filter'] is True
    assert kwargs['vad_parameters'] == {
        'min_silence_duration_ms': 1000, 'speech_pad_ms': 600,
        'threshold': 0.3}


def test_transcribe_vad_filter_false_reaches_model():
    kwargs = _transcribe_with({'vad_filter': False})
    assert kwargs['vad_filter'] is False
    assert kwargs['vad_parameters'] is None


def test_api_backend_forwards_vad_filter_false():
    """The tail upload must carry vad_filter=False, or a server that
    supports the switch never sees it and the pass is a normal one."""
    t = Transcriber()
    api_settings = dict(_local_settings(), backend='openai-api')
    with patch('transcriber._get_whisper_settings',
               return_value=api_settings), \
         patch.object(Transcriber, '_transcribe_via_api',
                      return_value=[]) as api:
        result = t.transcribe('/nonexistent.wav', vad_filter=False)
    assert result == []
    api.assert_called_once()
    assert api.call_args.kwargs['vad_filter'] is False
