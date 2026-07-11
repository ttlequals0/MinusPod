"""Tests for the configurable per-episode download size cap (#493).

The cap guards the disk against oversized or malicious enclosures. It must
be overridable via MAX_AUDIO_DOWNLOAD_MB, and tripping it must raise the
typed AudioTooLargeError (permanent, actionable) instead of the generic
return-None download failure.
"""
import os
from unittest.mock import MagicMock, patch

import pytest

from transcriber import Transcriber, _max_download_bytes
from utils.errors import AudioTooLargeError
from utils.safe_http import ResponseTooLargeError

MB = 1024 * 1024


class TestMaxDownloadBytes:
    def test_default_is_500mb(self, monkeypatch):
        monkeypatch.delenv('MAX_AUDIO_DOWNLOAD_MB', raising=False)
        assert _max_download_bytes() == 500 * MB

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv('MAX_AUDIO_DOWNLOAD_MB', '1200')
        assert _max_download_bytes() == 1200 * MB

    @pytest.mark.parametrize('raw', ['abc', '0', '-5', ''])
    def test_invalid_values_fall_back(self, monkeypatch, raw):
        monkeypatch.setenv('MAX_AUDIO_DOWNLOAD_MB', raw)
        assert _max_download_bytes() == 500 * MB


def _mock_response(content_length=None, chunks=(b'data',)):
    response = MagicMock()
    headers = {}
    if content_length is not None:
        headers['Content-Length'] = str(content_length)
    response.headers = headers
    response.iter_content.return_value = iter(chunks)
    response.raise_for_status.return_value = None
    return response


class TestDownloadAudioCap:
    def _download(self, response):
        with patch('transcriber.safe_get', return_value=response):
            return Transcriber().download_audio('https://example.com/ep.mp3')

    def test_content_length_over_cap_raises(self, monkeypatch):
        monkeypatch.delenv('MAX_AUDIO_DOWNLOAD_MB', raising=False)
        response = _mock_response(content_length=620 * MB)
        with pytest.raises(AudioTooLargeError, match='MAX_AUDIO_DOWNLOAD_MB'):
            self._download(response)

    def test_raised_cap_allows_bigger_file(self, monkeypatch):
        monkeypatch.setenv('MAX_AUDIO_DOWNLOAD_MB', '1000')
        response = _mock_response(content_length=620 * MB)
        path = self._download(response)
        assert path is not None
        os.unlink(path)

    def test_under_cap_downloads(self, monkeypatch):
        monkeypatch.delenv('MAX_AUDIO_DOWNLOAD_MB', raising=False)
        response = _mock_response(content_length=10 * MB)
        path = self._download(response)
        assert path is not None
        os.unlink(path)

    def test_stream_over_cap_raises_without_content_length(self, monkeypatch):
        # Chunked responses have no Content-Length; the stream cap must
        # still classify the failure as too-large, not a generic error.
        monkeypatch.delenv('MAX_AUDIO_DOWNLOAD_MB', raising=False)
        response = _mock_response(content_length=None)
        with patch('transcriber.stream_to_file_capped',
                   side_effect=ResponseTooLargeError('stream exceeds cap')), \
             patch('transcriber.safe_get', return_value=response):
            with pytest.raises(AudioTooLargeError, match='MAX_AUDIO_DOWNLOAD_MB'):
                Transcriber().download_audio('https://example.com/ep.mp3')

    def test_generic_failure_still_returns_none(self, monkeypatch):
        monkeypatch.delenv('MAX_AUDIO_DOWNLOAD_MB', raising=False)
        response = _mock_response(content_length=10 * MB)
        with patch('transcriber.stream_to_file_capped',
                   side_effect=OSError('disk error')), \
             patch('transcriber.safe_get', return_value=response):
            assert Transcriber().download_audio('https://example.com/ep.mp3') is None
