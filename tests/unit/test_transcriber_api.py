"""Tests for the OpenAI-compatible whisper API transcription backend."""
import os
import tempfile
from unittest.mock import patch, MagicMock

from transcriber import (
    Transcriber, _get_whisper_settings, _get_whisper_compute_type,
    calculate_optimal_chunk_duration,
)
from config import (
    API_CHUNK_DURATION_SECONDS,
    WHISPER_BACKEND_LOCAL,
    WHISPER_BACKEND_API,
)


def _mock_db_with_settings(settings_dict):
    """Create a mock Database that returns values from settings_dict."""
    mock_db = MagicMock()
    mock_db.get_setting.side_effect = lambda key: settings_dict.get(key)
    mock_db.get_secret.side_effect = lambda key: settings_dict.get(key)
    return mock_db


class TestGetWhisperSettings:
    """Tests for the _get_whisper_settings helper."""

    def test_returns_api_backend_from_db(self):
        mock_db = _mock_db_with_settings({
            'whisper_backend': WHISPER_BACKEND_API,
            'whisper_api_base_url': 'http://localhost:8765/v1',
            'whisper_api_key': 'sk-test',
            'whisper_api_model': 'large-v3',
        })
        with patch('database.Database', return_value=mock_db):
            settings = _get_whisper_settings()
        assert settings['backend'] == WHISPER_BACKEND_API
        assert settings['api_base_url'] == 'http://localhost:8765/v1'
        assert settings['api_key'] == 'sk-test'
        assert settings['api_model'] == 'large-v3'

    def test_returns_local_backend_from_db(self):
        mock_db = _mock_db_with_settings({'whisper_backend': WHISPER_BACKEND_LOCAL})
        with patch('database.Database', return_value=mock_db):
            settings = _get_whisper_settings()
        assert settings['backend'] == WHISPER_BACKEND_LOCAL

    @patch.dict(os.environ, {'WHISPER_BACKEND': WHISPER_BACKEND_API})
    def test_falls_back_to_env_var(self):
        with patch('database.Database', side_effect=Exception("no db")):
            settings = _get_whisper_settings()
        assert settings['backend'] == WHISPER_BACKEND_API

    def test_defaults_to_local(self):
        env = os.environ.copy()
        env.pop('WHISPER_BACKEND', None)
        with patch.dict(os.environ, env, clear=True):
            with patch('database.Database', side_effect=Exception("no db")):
                settings = _get_whisper_settings()
        assert settings['backend'] == WHISPER_BACKEND_LOCAL

    def test_reads_all_settings_in_one_call(self):
        """Verify all 4 settings are read from a single Database instance."""
        mock_db = _mock_db_with_settings({
            'whisper_backend': WHISPER_BACKEND_API,
            'whisper_api_base_url': 'http://example.com/v1',
            'whisper_api_key': 'key123',
            'whisper_api_model': 'model-x',
        })
        with patch('database.Database', return_value=mock_db) as mock_cls:
            settings = _get_whisper_settings()
            # Only one Database() instantiation
            assert mock_cls.call_count == 1
        assert settings['api_base_url'] == 'http://example.com/v1'
        assert settings['api_key'] == 'key123'
        assert settings['api_model'] == 'model-x'


class TestApiChunkDuration:
    """Tests for calculate_optimal_chunk_duration with API backend."""

    def test_returns_api_chunk_duration(self):
        duration, reason = calculate_optimal_chunk_duration(
            'small', 'cuda', whisper_backend=WHISPER_BACKEND_API
        )
        assert duration == API_CHUNK_DURATION_SECONDS
        assert 'API backend' in reason

    @patch('transcriber.get_available_memory_gb', return_value=(8.0, 'GPU'))
    def test_returns_memory_based_for_local(self, mock_mem):
        duration, reason = calculate_optimal_chunk_duration(
            'small', 'cuda', whisper_backend=WHISPER_BACKEND_LOCAL
        )
        assert duration != API_CHUNK_DURATION_SECONDS
        assert 'GPU' in reason


class TestTranscribeViaApi:
    """Tests for Transcriber._transcribe_via_api response parsing."""

    def _make_api_response(self, segments):
        """Create a mock verbose_json response."""
        return {
            'text': ' '.join(s.get('text', '') for s in segments),
            'segments': segments,
        }

    def _make_settings(self, **overrides):
        """Create whisper settings dict with defaults."""
        settings = {
            'backend': WHISPER_BACKEND_API,
            'api_base_url': 'http://localhost:8765/v1',
            'api_key': '',
            'api_model': 'whisper-1',
        }
        settings.update(overrides)
        return settings

    def test_parses_verbose_json_segments(self):
        with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as f:
            f.write(b'fake audio data' * 200)
            temp_path = f.name

        try:
            api_segments = [
                {
                    'start': 0.0, 'end': 5.0,
                    'text': ' Hello world',
                    'words': [
                        {'word': ' Hello', 'start': 0.0, 'end': 0.5},
                        {'word': ' world', 'start': 0.5, 'end': 1.0},
                    ],
                },
                {
                    'start': 5.0, 'end': 10.0,
                    'text': ' This is a test',
                    'words': [],
                },
            ]

            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = self._make_api_response(api_segments)

            with patch('transcriber.safe_post', return_value=mock_response):
                transcriber = Transcriber()
                transcriber.preprocess_audio = MagicMock(return_value=None)
                result = transcriber._transcribe_via_api(
                    temp_path, 'TestPodcast', self._make_settings()
                )

            assert result is not None
            assert len(result) == 2
            assert result[0]['start'] == 0.0
            assert result[0]['end'] == 5.0
            assert result[0]['text'] == 'Hello world'
            assert len(result[0]['words']) == 2
            assert result[1]['words'] == []
        finally:
            os.unlink(temp_path)

    def test_returns_none_on_missing_base_url(self):
        with patch('transcriber.safe_post') as mock_post:
            transcriber = Transcriber()
            result = transcriber._transcribe_via_api(
                '/tmp/test.wav', whisper_settings=self._make_settings(api_base_url='')
            )
            assert result is None
            mock_post.assert_not_called()

    def test_returns_none_on_failed_request(self):
        """post_with_retry returns None on non-200 errors."""
        with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as f:
            f.write(b'fake' * 512)
            temp_path = f.name

        try:
            with patch('transcriber.safe_post', return_value=None):
                transcriber = Transcriber()
                transcriber.preprocess_audio = MagicMock(return_value=None)
                result = transcriber._transcribe_via_api(
                    temp_path, whisper_settings=self._make_settings()
                )
                assert result is None
        finally:
            os.unlink(temp_path)

    def test_sends_auth_header_when_key_set(self):
        with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as f:
            f.write(b'fake' * 512)
            temp_path = f.name

        try:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {'segments': []}

            with patch('transcriber.safe_post', return_value=mock_response) as mock_post:
                transcriber = Transcriber()
                transcriber.preprocess_audio = MagicMock(return_value=None)
                transcriber._transcribe_via_api(
                    temp_path, whisper_settings=self._make_settings(api_key='sk-test-key')
                )
                call_kwargs = mock_post.call_args
                assert call_kwargs.kwargs['headers']['Authorization'] == 'Bearer sk-test-key'
        finally:
            os.unlink(temp_path)

    def test_no_auth_header_when_key_empty(self):
        with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as f:
            f.write(b'fake' * 512)
            temp_path = f.name

        try:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {'segments': []}

            with patch('transcriber.safe_post', return_value=mock_response) as mock_post:
                transcriber = Transcriber()
                transcriber.preprocess_audio = MagicMock(return_value=None)
                transcriber._transcribe_via_api(
                    temp_path, whisper_settings=self._make_settings(api_key='')
                )
                call_kwargs = mock_post.call_args
                assert 'Authorization' not in call_kwargs.kwargs['headers']
        finally:
            os.unlink(temp_path)

    def test_filters_empty_segments(self):
        with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as f:
            f.write(b'fake' * 512)
            temp_path = f.name

        try:
            api_segments = [
                {'start': 0.0, 'end': 5.0, 'text': ' Hello', 'words': []},
                {'start': 5.0, 'end': 10.0, 'text': '', 'words': []},
                {'start': 10.0, 'end': 15.0, 'text': '   ', 'words': []},
                {'start': 15.0, 'end': 20.0, 'text': ' World', 'words': []},
            ]

            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = self._make_api_response(api_segments)

            with patch('transcriber.safe_post', return_value=mock_response):
                transcriber = Transcriber()
                transcriber.preprocess_audio = MagicMock(return_value=None)
                result = transcriber._transcribe_via_api(
                    temp_path, whisper_settings=self._make_settings()
                )

            assert len(result) == 2
            assert result[0]['text'] == 'Hello'
            assert result[1]['text'] == 'World'
        finally:
            os.unlink(temp_path)


class TestTranscriberBackendDispatch:
    """Tests for the backend dispatch in transcribe()."""

    @patch('transcriber._get_whisper_settings', return_value={
        'backend': WHISPER_BACKEND_API,
        'api_base_url': 'http://localhost:8765/v1',
        'api_key': '', 'api_model': 'whisper-1',
    })
    @patch.object(Transcriber, '_transcribe_via_api', return_value=[{'start': 0, 'end': 5, 'text': 'test', 'words': []}])
    def test_dispatches_to_api_when_openai_api(self, mock_api, mock_settings):
        transcriber = Transcriber()
        result = transcriber.transcribe('/tmp/test.wav', 'TestPodcast')
        mock_api.assert_called_once()
        assert result is not None

    @patch('transcriber._get_whisper_settings', return_value={
        'backend': WHISPER_BACKEND_LOCAL,
        'api_base_url': '', 'api_key': '', 'api_model': 'whisper-1',
    })
    def test_does_not_dispatch_to_api_when_local(self, mock_settings):
        transcriber = Transcriber()
        with patch.object(Transcriber, '_transcribe_via_api') as mock_api:
            try:
                transcriber.transcribe('/tmp/test.wav')
            except Exception:
                pass
            mock_api.assert_not_called()


class TestComputeTypeSettings:
    """Tests for WHISPER_COMPUTE_TYPE resolution in _get_whisper_compute_type."""

    def test_defaults_to_auto_when_unset(self):
        env = {k: v for k, v in os.environ.items() if k != 'WHISPER_COMPUTE_TYPE'}
        with patch.dict(os.environ, env, clear=True):
            with patch('database.Database', side_effect=Exception("no db")):
                assert _get_whisper_compute_type() == 'auto'

    @patch.dict(os.environ, {'WHISPER_COMPUTE_TYPE': 'int8'})
    def test_env_var_override(self):
        with patch('database.Database', side_effect=Exception("no db")):
            assert _get_whisper_compute_type() == 'int8'

    def test_db_overrides_env(self):
        mock_db = _mock_db_with_settings({'whisper_compute_type': 'float32'})
        with patch.dict(os.environ, {'WHISPER_COMPUTE_TYPE': 'int8'}):
            with patch('database.Database', return_value=mock_db):
                assert _get_whisper_compute_type() == 'float32'

    @patch.dict(os.environ, {'WHISPER_COMPUTE_TYPE': 'nonsense'})
    def test_unknown_value_falls_back_to_auto(self):
        with patch('database.Database', side_effect=Exception("no db")):
            assert _get_whisper_compute_type() == 'auto'

    def test_settings_dict_does_not_include_compute_type(self):
        """Ensures compute_type is kept out of the shared settings dict
        that also holds api_key (CodeQL py/clear-text-logging defense)."""
        with patch('database.Database', side_effect=Exception("no db")):
            settings = _get_whisper_settings()
        assert 'compute_type' not in settings


class TestWhisperModelSingletonFallback:
    """Tests for WhisperModelSingleton float16 -> int8_float16 -> int8 -> float32 fallback."""

    def _reset_singleton(self):
        from transcriber import WhisperModelSingleton
        WhisperModelSingleton._instance = None
        WhisperModelSingleton._base_model = None
        WhisperModelSingleton._current_model_name = None
        WhisperModelSingleton._needs_reload = False

    def _settings(self):
        return {
            'backend': WHISPER_BACKEND_LOCAL,
            'api_base_url': '', 'api_key': '', 'api_model': 'whisper-1',
            'language': 'en',
        }

    @patch.dict(os.environ, {'WHISPER_DEVICE': 'cuda'})
    def test_float16_on_cuda_succeeds_first_try(self):
        self._reset_singleton()
        from transcriber import WhisperModelSingleton
        with patch('transcriber._get_whisper_settings', return_value=self._settings()), \
             patch('transcriber._get_whisper_compute_type', return_value='auto'), \
             patch('transcriber.ctranslate2.get_cuda_device_count', return_value=1), \
             patch('transcriber.WhisperModel') as mock_model, \
             patch('transcriber.BatchedInferencePipeline'), \
             patch.object(WhisperModelSingleton, 'get_configured_model', return_value='small'), \
             patch('transcriber.get_gpu_memory_info', return_value=None):
            WhisperModelSingleton.get_instance()
            mock_model.assert_called_once()
            _, kwargs = mock_model.call_args
            assert kwargs['compute_type'] == 'float16'
            assert kwargs['device'] == 'cuda'

    @patch.dict(os.environ, {'WHISPER_DEVICE': 'cuda'})
    def test_cuda_float16_failure_falls_back_to_int8_float16(self):
        self._reset_singleton()
        from transcriber import WhisperModelSingleton
        call_log = []

        def fake_model(*args, **kwargs):
            call_log.append(kwargs['compute_type'])
            if kwargs['compute_type'] == 'float16':
                raise RuntimeError("no fp16 support")
            return MagicMock()

        with patch('transcriber._get_whisper_settings', return_value=self._settings()), \
             patch('transcriber._get_whisper_compute_type', return_value='auto'), \
             patch('transcriber.ctranslate2.get_cuda_device_count', return_value=1), \
             patch('transcriber.WhisperModel', side_effect=fake_model), \
             patch('transcriber.BatchedInferencePipeline'), \
             patch.object(WhisperModelSingleton, 'get_configured_model', return_value='small'), \
             patch('transcriber.get_gpu_memory_info', return_value=None):
            WhisperModelSingleton.get_instance()
        assert call_log == ['float16', 'int8_float16']

    @patch.dict(os.environ, {'WHISPER_DEVICE': 'cuda'})
    def test_cuda_fallback_walks_chain_to_float32(self):
        """Pascal consumer (CC 6.1) fails float16 and int8_float16; should land on int8."""
        self._reset_singleton()
        from transcriber import WhisperModelSingleton
        call_log = []

        def fake_model(*args, **kwargs):
            call_log.append(kwargs['compute_type'])
            if kwargs['compute_type'] in ('float16', 'int8_float16'):
                raise RuntimeError("unsupported on this GPU")
            return MagicMock()

        with patch('transcriber._get_whisper_settings', return_value=self._settings()), \
             patch('transcriber._get_whisper_compute_type', return_value='auto'), \
             patch('transcriber.ctranslate2.get_cuda_device_count', return_value=1), \
             patch('transcriber.WhisperModel', side_effect=fake_model), \
             patch('transcriber.BatchedInferencePipeline'), \
             patch.object(WhisperModelSingleton, 'get_configured_model', return_value='small'), \
             patch('transcriber.get_gpu_memory_info', return_value=None):
            WhisperModelSingleton.get_instance()
        assert call_log == ['float16', 'int8_float16', 'int8']

    @patch.dict(os.environ, {'WHISPER_DEVICE': 'cuda'})
    def test_cuda_fallback_raises_when_entire_chain_fails(self):
        self._reset_singleton()
        from transcriber import WhisperModelSingleton

        def fake_model(*args, **kwargs):
            raise RuntimeError(f"boom-{kwargs['compute_type']}")

        with patch('transcriber._get_whisper_settings', return_value=self._settings()), \
             patch('transcriber._get_whisper_compute_type', return_value='auto'), \
             patch('transcriber.ctranslate2.get_cuda_device_count', return_value=1), \
             patch('transcriber.WhisperModel', side_effect=fake_model), \
             patch('transcriber.BatchedInferencePipeline'), \
             patch.object(WhisperModelSingleton, 'get_configured_model', return_value='small'), \
             patch('transcriber.get_gpu_memory_info', return_value=None):
            import pytest
            with pytest.raises(RuntimeError, match="boom-float32"):
                WhisperModelSingleton.get_instance()

    @patch.dict(os.environ, {'WHISPER_DEVICE': 'cuda'})
    def test_explicit_non_float16_reraises_without_fallback(self):
        """Explicit int8_float16 failure must not silently fall back."""
        self._reset_singleton()
        from transcriber import WhisperModelSingleton
        call_log = []

        def fake_model(*args, **kwargs):
            call_log.append(kwargs['compute_type'])
            raise RuntimeError("unsupported")

        with patch('transcriber._get_whisper_settings', return_value=self._settings()), \
             patch('transcriber._get_whisper_compute_type', return_value='int8_float16'), \
             patch('transcriber.ctranslate2.get_cuda_device_count', return_value=1), \
             patch('transcriber.WhisperModel', side_effect=fake_model), \
             patch('transcriber.BatchedInferencePipeline'), \
             patch.object(WhisperModelSingleton, 'get_configured_model', return_value='small'), \
             patch('transcriber.get_gpu_memory_info', return_value=None):
            import pytest
            with pytest.raises(RuntimeError, match="unsupported"):
                WhisperModelSingleton.get_instance()
        assert call_log == ['int8_float16']

    @patch.dict(os.environ, {'WHISPER_DEVICE': 'cpu'})
    def test_cpu_auto_resolves_to_int8_without_fallback(self):
        self._reset_singleton()
        from transcriber import WhisperModelSingleton
        with patch('transcriber._get_whisper_settings', return_value=self._settings()), \
             patch('transcriber._get_whisper_compute_type', return_value='auto'), \
             patch('transcriber.WhisperModel') as mock_model, \
             patch('transcriber.BatchedInferencePipeline'), \
             patch.object(WhisperModelSingleton, 'get_configured_model', return_value='small'), \
             patch('transcriber.get_gpu_memory_info', return_value=None):
            WhisperModelSingleton.get_instance()
            _, kwargs = mock_model.call_args
            assert kwargs['compute_type'] == 'int8'
            assert kwargs['device'] == 'cpu'
