"""Tests for the configurable per-episode download size cap (#493).

The cap guards the disk against oversized or malicious enclosures. It must
be overridable via MAX_AUDIO_DOWNLOAD_MB, and tripping it must raise the
typed AudioTooLargeError (permanent, actionable) instead of the generic
return-None download failure.
"""
import os
from unittest.mock import MagicMock, patch

import pytest

from transcriber import Transcriber, _max_download_mb
from utils.errors import AudioTooLargeError
from utils.safe_http import ResponseTooLargeError

MB = 1024 * 1024


@pytest.fixture(autouse=True)
def _no_db_setting(monkeypatch):
    # Exercise the env-seed path of the env-backed cap; a DB row from
    # another test module's Database singleton must not leak in.
    import config as _config_mod
    monkeypatch.setattr(_config_mod, '_db_setting', lambda key: None)


@pytest.fixture(autouse=True)
def _clear_cap_env(monkeypatch):
    monkeypatch.delenv('MAX_AUDIO_DOWNLOAD_MB', raising=False)


class TestMaxDownloadMb:
    def test_default_is_500mb(self):
        assert _max_download_mb() == 500

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv('MAX_AUDIO_DOWNLOAD_MB', '1200')
        assert _max_download_mb() == 1200

    @pytest.mark.parametrize('raw', ['abc', '0', '-5', ''])
    def test_invalid_values_fall_back(self, monkeypatch, raw):
        monkeypatch.setenv('MAX_AUDIO_DOWNLOAD_MB', raw)
        assert _max_download_mb() == 500


def _mock_response(content_length=None):
    response = MagicMock()
    headers = {}
    if content_length is not None:
        headers['Content-Length'] = str(content_length)
    response.headers = headers
    return response


class TestDownloadAudioCap:
    def _download(self, response, stream_error=None):
        # side_effect=None leaves the stream mock a no-op; the happy-path
        # tests only assert a temp path comes back.
        with patch('transcriber.safe_get', return_value=response), \
             patch('transcriber.stream_to_file_capped', side_effect=stream_error):
            return Transcriber().download_audio('https://example.com/ep.mp3')

    def test_content_length_over_cap_raises(self):
        response = _mock_response(content_length=620 * MB)
        with pytest.raises(AudioTooLargeError, match='MAX_AUDIO_DOWNLOAD_MB'):
            self._download(response)

    def test_raised_cap_allows_bigger_file(self, monkeypatch):
        monkeypatch.setenv('MAX_AUDIO_DOWNLOAD_MB', '1000')
        response = _mock_response(content_length=620 * MB)
        path = self._download(response)
        assert path is not None
        os.unlink(path)

    def test_under_cap_downloads(self):
        response = _mock_response(content_length=10 * MB)
        path = self._download(response)
        assert path is not None
        os.unlink(path)

    def test_stream_over_cap_raises_without_content_length(self):
        # Chunked responses have no Content-Length; the stream cap must
        # still classify the failure as too-large, not a generic error.
        response = _mock_response(content_length=None)
        with pytest.raises(AudioTooLargeError, match='MAX_AUDIO_DOWNLOAD_MB'):
            self._download(response,
                           stream_error=ResponseTooLargeError('stream exceeds cap'))

    def test_generic_failure_still_returns_none(self):
        response = _mock_response(content_length=10 * MB)
        assert self._download(response, stream_error=OSError('disk error')) is None

    def test_stream_cap_uses_env_value(self, monkeypatch):
        # The env override must reach the stream cap too, not only the
        # Content-Length pre-check; chunked responses rely on it alone.
        monkeypatch.setenv('MAX_AUDIO_DOWNLOAD_MB', '1000')
        response = _mock_response(content_length=None)
        with patch('transcriber.safe_get', return_value=response), \
             patch('transcriber.stream_to_file_capped') as stream_mock:
            path = Transcriber().download_audio('https://example.com/ep.mp3')
        assert path is not None
        os.unlink(path)
        assert stream_mock.call_args.args[2] == 1000 * MB

    def test_hint_not_doubled_on_rewrap(self):
        original = AudioTooLargeError('Audio file is 620.0MB, over the 500MB download cap')
        rewrapped = AudioTooLargeError(str(original))
        assert str(rewrapped).count('MAX_AUDIO_DOWNLOAD_MB') == 1
