"""Tests for the whisper remote transcriber connection test (#544).

Covers the probe helpers in transcriber.py and the
/settings/providers/whisper/test-connection endpoint.
"""
import json
import wave
import io
from unittest.mock import patch, MagicMock

import pytest
import requests as requests_lib

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('whisper_conn_test_', passphrase='whisper-conn-test-pass')

from main_app import app  # noqa: E402
from transcriber import (  # noqa: E402
    _probe_wav_bytes,
    _probe_upload,
    probe_transcription_endpoint,
)
from utils.connection_probe import PROBE_RESPONSE_CAP_BYTES  # noqa: E402
from utils.url import SSRFError  # noqa: E402

BASE = '/api/v1/settings/providers/whisper/test-connection'

SAVED = {
    'backend': 'openai-api',
    'api_base_url': 'http://transcriber:8001/v3',
    'api_key': 'sk-saved',
    'api_model': 'whisper-1',
    'language': 'en',
    'skip_flac_compression': True,
}


@pytest.fixture(autouse=True)
def _clean_whisper_env(monkeypatch):
    # _get_whisper_settings falls back to these; a developer or CI job with
    # a live whisper setup exported must not leak it into the tests.
    for var in ('WHISPER_BACKEND', 'WHISPER_API_BASE_URL', 'WHISPER_API_KEY',
                'WHISPER_API_MODEL', 'SKIP_FLAC_COMPRESSION'):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c


def _response(status, body=b'', json_body=None):
    if json_body is not None:
        body = json.dumps(json_body).encode()
    r = MagicMock()
    r.status_code = status
    r.iter_content = lambda chunk_size: iter([body])
    return r


class TestProbeWav:
    def test_is_valid_wav(self):
        data = _probe_wav_bytes()
        with wave.open(io.BytesIO(data), 'rb') as w:
            assert w.getnchannels() == 1
            assert w.getsampwidth() == 2
            assert w.getframerate() == 16000
            assert w.getnframes() == 16000

    def test_is_not_suspiciously_small(self):
        # The real upload path refuses files under 1024 bytes; the probe
        # should stay comfortably above any similar server-side floor.
        assert len(_probe_wav_bytes()) > 1024


class TestProbeUpload:
    def test_skip_flac_sends_wav(self):
        filename, audio = _probe_upload(skip_flac_compression=True)
        assert filename == 'probe.wav'
        assert audio == _probe_wav_bytes()

    def test_flac_mode_encodes(self):
        def fake_ffmpeg(cmd, **kwargs):
            with open(cmd[-1], 'wb') as fh:
                fh.write(b'fLaC-probe-data')
            return MagicMock(returncode=0)

        with patch('transcriber.tracked_run', side_effect=fake_ffmpeg):
            filename, audio = _probe_upload(skip_flac_compression=False)
        assert filename == 'probe.flac'
        assert audio == b'fLaC-probe-data'

    def test_flac_encode_failure_falls_back_to_wav(self):
        # Mirrors the real upload path, which sends WAV when ffmpeg fails.
        with patch('transcriber.tracked_run',
                   return_value=MagicMock(returncode=1)):
            filename, audio = _probe_upload(skip_flac_compression=False)
        assert filename == 'probe.wav'
        assert audio == _probe_wav_bytes()


class TestProbeTranscriptionEndpoint:
    def test_success_json_response(self):
        with patch('transcriber.safe_post',
                   return_value=_response(200, json_body={'text': ''})) as sp:
            result = probe_transcription_endpoint(
                'http://transcriber:8001/v3', api_key='sk-x', model='whisper-1')
        assert result['ok'] is True
        assert result['reachable'] is True
        assert result['status'] == 200
        assert sp.call_args[0][0] == 'http://transcriber:8001/v3/audio/transcriptions'
        headers = sp.call_args[1]['headers']
        assert headers['Authorization'] == 'Bearer sk-x'
        form = sp.call_args[1]['data']
        assert form['model'] == 'whisper-1'
        assert form['timestamp_granularities[]'] == ['segment']
        assert sp.call_args[1]['files']['file'][0] == 'probe.wav'

    def test_trailing_slash_stripped(self):
        with patch('transcriber.safe_post',
                   return_value=_response(200, json_body={})) as sp:
            probe_transcription_endpoint('http://transcriber:8001/v3/')
        assert sp.call_args[0][0] == 'http://transcriber:8001/v3/audio/transcriptions'

    def test_no_key_sends_no_auth_header(self):
        with patch('transcriber.safe_post',
                   return_value=_response(200, json_body={})) as sp:
            probe_transcription_endpoint('http://transcriber:8001/v3')
        assert 'Authorization' not in sp.call_args[1]['headers']

    def test_flac_mode_uploads_flac_filename(self):
        def fake_ffmpeg(cmd, **kwargs):
            with open(cmd[-1], 'wb') as fh:
                fh.write(b'fLaC')
            return MagicMock(returncode=0)

        with patch('transcriber.tracked_run', side_effect=fake_ffmpeg), \
             patch('transcriber.safe_post',
                   return_value=_response(200, json_body={})) as sp:
            probe_transcription_endpoint('http://transcriber:8001/v3',
                                         skip_flac_compression=False)
        assert sp.call_args[1]['files']['file'][0] == 'probe.flac'

    def test_200_non_json_is_not_ok(self):
        with patch('transcriber.safe_post',
                   return_value=_response(200, body=b'<html>hi</html>')):
            result = probe_transcription_endpoint('http://some-web-server')
        assert result['ok'] is False
        assert result['reachable'] is True
        assert 'OpenAI-compatible' in result['detail']

    def test_200_oversized_body_is_not_ok(self):
        huge = b'x' * (PROBE_RESPONSE_CAP_BYTES + 1)
        with patch('transcriber.safe_post',
                   return_value=_response(200, body=huge)):
            result = probe_transcription_endpoint('http://some-file-server')
        assert result['ok'] is False
        assert result['reachable'] is True

    def test_401_points_at_api_key(self):
        with patch('transcriber.safe_post', return_value=_response(401)):
            result = probe_transcription_endpoint('http://transcriber:8001/v3')
        assert result['ok'] is False
        assert result['reachable'] is True
        assert result['status'] == 401
        assert 'API key' in result['detail']

    def test_401_with_key_names_saved_key(self):
        with patch('transcriber.safe_post', return_value=_response(401)):
            result = probe_transcription_endpoint('http://transcriber:8001/v3',
                                                  api_key='sk-x')
        assert 'rejected the saved API key' in result['detail']

    def test_404_points_at_path(self):
        with patch('transcriber.safe_post', return_value=_response(404)):
            result = probe_transcription_endpoint('http://transcriber:8001/wrong')
        assert result['ok'] is False
        assert result['reachable'] is True
        assert '/v3' in result['detail']

    def test_400_includes_body_snippet(self):
        with patch('transcriber.safe_post',
                   return_value=_response(400, body=b'{"error": "unsupported model"}')):
            result = probe_transcription_endpoint('http://transcriber:8001/v3')
        assert result['ok'] is False
        assert result['reachable'] is True
        assert 'unsupported model' in result['detail']

    def test_error_snippet_truncated(self):
        with patch('transcriber.safe_post',
                   return_value=_response(500, body=b'x' * 5000)):
            result = probe_transcription_endpoint('http://transcriber:8001/v3')
        assert len(result['detail']) < 400

    def test_connection_error_unreachable(self):
        with patch('transcriber.safe_post',
                   side_effect=requests_lib.ConnectionError('refused')):
            result = probe_transcription_endpoint('http://transcriber:8001/v3')
        assert result['ok'] is False
        assert result['reachable'] is False
        assert 'connect' in result['detail'].lower()

    def test_connect_timeout_unreachable(self):
        with patch('transcriber.safe_post',
                   side_effect=requests_lib.ConnectTimeout('slow handshake')):
            result = probe_transcription_endpoint('http://transcriber:8001/v3')
        assert result['ok'] is False
        assert result['reachable'] is False

    def test_read_timeout_is_reachable(self):
        # The connection succeeded; inference was just slow (e.g. a server
        # cold-loading its model). Must not present as "server down".
        with patch('transcriber.safe_post',
                   side_effect=requests_lib.ReadTimeout('slow inference')):
            result = probe_transcription_endpoint('http://transcriber:8001/v3')
        assert result['ok'] is False
        assert result['reachable'] is True
        assert 'try again' in result['detail']

    def test_ssrf_rejected(self):
        with patch('transcriber.safe_post', side_effect=SSRFError('nope')):
            result = probe_transcription_endpoint('http://169.254.169.254/v1')
        assert result['ok'] is False
        assert result['reachable'] is False
        assert 'SSRF' in result['detail']


class TestEndpoint:
    def _post(self, client, body=None):
        return client.post(BASE, data=json.dumps(body if body is not None else {}),
                           content_type='application/json')

    def test_no_base_url_configured(self, client):
        r = self._post(client)
        assert r.status_code == 200
        data = r.get_json()
        assert data['ok'] is False
        assert 'base URL' in data['detail']

    def test_empty_body_base_url_never_falls_back_to_saved(self, client):
        # The form always sends the field's current value; an empty field
        # must not silently probe the previously saved URL.
        with patch('api.providers.transcriber._get_whisper_settings',
                   return_value=dict(SAVED)), \
             patch('api.providers.transcriber.probe_transcription_endpoint') as probe:
            r = self._post(client, {'baseUrl': '', 'model': 'whisper-1'})
        assert r.status_code == 200
        assert 'base URL' in r.get_json()['detail']
        probe.assert_not_called()

    def test_body_values_reach_probe(self, client):
        with patch('api.providers.transcriber._get_whisper_settings',
                   return_value=dict(SAVED)), \
             patch('api.providers.transcriber.probe_transcription_endpoint',
                   return_value={'ok': True, 'reachable': True,
                                 'status': 200, 'detail': 'Connected.'}) as probe:
            r = self._post(client, {'baseUrl': 'http://transcriber:8001/v3',
                                    'model': 'large-v3',
                                    'skipFlacCompression': False})
        assert r.status_code == 200
        assert r.get_json()['ok'] is True
        args, kwargs = probe.call_args
        assert args[0] == 'http://transcriber:8001/v3'
        assert kwargs['model'] == 'large-v3'
        assert kwargs['skip_flac_compression'] is False

    def test_saved_key_sent_only_to_saved_server(self, client):
        # Same scheme/host/port as the saved base URL: key goes out.
        with patch('api.providers.transcriber._get_whisper_settings',
                   return_value=dict(SAVED)), \
             patch('api.providers.transcriber.probe_transcription_endpoint',
                   return_value={'ok': True, 'reachable': True,
                                 'status': 200, 'detail': 'Connected.'}) as probe:
            self._post(client, {'baseUrl': 'http://transcriber:8001/v1'})
        assert probe.call_args[1]['api_key'] == 'sk-saved'

    def test_saved_key_withheld_from_other_server(self, client):
        # Different host: the stored key must not be exfiltratable by
        # "testing" an attacker-controlled URL.
        with patch('api.providers.transcriber._get_whisper_settings',
                   return_value=dict(SAVED)), \
             patch('api.providers.transcriber.probe_transcription_endpoint',
                   return_value={'ok': True, 'reachable': True,
                                 'status': 200, 'detail': 'Connected.'}) as probe:
            self._post(client, {'baseUrl': 'http://evil.example.com/v3'})
        assert probe.call_args[1]['api_key'] == ''

    def test_ssrf_base_url_rejected(self, client):
        # No probe mock: SSRF validation inside safe_post is local (IP
        # check), so the cloud metadata address is refused before any
        # network I/O.
        r = self._post(client, {'baseUrl': 'http://169.254.169.254/latest'})
        assert r.status_code == 200
        data = r.get_json()
        assert data['ok'] is False
        assert data['reachable'] is False
        assert 'SSRF' in data['detail']

    def test_non_string_base_url_rejected(self, client):
        r = self._post(client, {'baseUrl': 123})
        assert r.status_code == 400

    def test_non_string_model_rejected(self, client):
        r = self._post(client, {'baseUrl': 'http://transcriber:8001/v3',
                                'model': 123})
        assert r.status_code == 400

    def test_non_bool_skip_flac_rejected(self, client):
        r = self._post(client, {'baseUrl': 'http://transcriber:8001/v3',
                                'skipFlacCompression': 'yes'})
        assert r.status_code == 400
