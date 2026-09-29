"""The local faster-whisper stack is optional for the API backend (#795)."""
import logging
import os
import subprocess
import sys
from unittest.mock import patch

import pytest

import transcriber
from config import WHISPER_BACKEND_API, WHISPER_BACKEND_LOCAL
from utils.errors import LocalTranscriptionUnavailableError

REPO_ROOT = os.path.join(os.path.dirname(__file__), '..', '..')


def _settings(backend):
    return {'backend': backend, 'api_base_url': 'http://whisper.example.com',
            'api_key': 'k', 'api_model': 'whisper-1', 'language': 'en'}


@pytest.fixture
def missing_stack(monkeypatch):
    monkeypatch.setattr(transcriber, '_LOCAL_IMPORT_ERROR',
                        ImportError('No module named faster_whisper'))


def test_module_imports_without_local_packages():
    script = (
        "import sys; sys.path.insert(0, 'src');"
        "sys.modules['faster_whisper'] = None; sys.modules['ctranslate2'] = None;"
        "import transcriber;"
        "assert transcriber._LOCAL_IMPORT_ERROR is not None;"
        "assert transcriber.ctranslate2 is None;"
        "assert transcriber.WhisperModel is None;"
        "assert transcriber.BatchedInferencePipeline is None"
    )
    result = subprocess.run([sys.executable, '-c', script], cwd=REPO_ROOT,
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_local_transcribe_raises_actionable_error(missing_stack):
    with patch('transcriber._get_whisper_settings',
               return_value=_settings(WHISPER_BACKEND_LOCAL)):
        with pytest.raises(LocalTranscriptionUnavailableError) as exc:
            transcriber.Transcriber().transcribe('/nonexistent.mp3')
    assert 'WHISPER_BACKEND=openai-api' in str(exc.value)
    assert 'No module named faster_whisper' in str(exc.value)


def test_local_transcribe_chunked_raises_actionable_error(missing_stack):
    t = transcriber.Transcriber()
    with patch('transcriber._get_whisper_settings',
               return_value=_settings(WHISPER_BACKEND_LOCAL)), \
            patch.object(t, 'get_audio_duration', return_value=600.0):
        with pytest.raises(LocalTranscriptionUnavailableError, match='WHISPER_BACKEND=openai-api'):
            t.transcribe_chunked('/nonexistent.mp3')


def test_model_singleton_backstop(missing_stack):
    with pytest.raises(LocalTranscriptionUnavailableError):
        transcriber.WhisperModelSingleton.get_instance()


def test_error_text_avoids_classifier_trigger_words(missing_stack):
    with pytest.raises(LocalTranscriptionUnavailableError) as exc:
        transcriber._require_local_transcription()
    message = str(exc.value).lower()
    assert 'cuda' not in message
    assert 'oom' not in message


def test_api_backend_skips_the_guard(missing_stack):
    t = transcriber.Transcriber()
    sentinel = [{'start': 0.0, 'end': 1.0, 'text': 'hi'}]
    with patch('transcriber._get_whisper_settings',
               return_value=_settings(WHISPER_BACKEND_API)), \
            patch.object(t, '_transcribe_via_api', return_value=sentinel), \
            patch.object(t, '_transcribe_chunked_parallel_api', return_value=sentinel), \
            patch.object(t, 'get_audio_duration', return_value=600.0):
        assert t.transcribe('/nonexistent.mp3') is sentinel
        assert t.transcribe_chunked('/nonexistent.mp3') is sentinel


def test_local_health_reports_missing_packages(missing_stack):
    health = transcriber.get_local_transcriber_health()
    assert health['available'] is False
    assert 'faster-whisper' in health['reason']


def test_init_warns_when_local_backend_lacks_packages(missing_stack, caplog):
    with patch('transcriber._get_whisper_settings',
               return_value=_settings(WHISPER_BACKEND_LOCAL)), \
            caplog.at_level(logging.WARNING, logger='transcriber'):
        transcriber.Transcriber()
    warnings = [r for r in caplog.records if 'Local Whisper packages are missing' in r.message]
    assert len(warnings) == 1


def test_init_silent_on_api_backend(missing_stack, caplog):
    with patch('transcriber._get_whisper_settings',
               return_value=_settings(WHISPER_BACKEND_API)), \
            caplog.at_level(logging.WARNING, logger='transcriber'):
        transcriber.Transcriber()
    assert not any('Local Whisper packages are missing' in r.message for r in caplog.records)
