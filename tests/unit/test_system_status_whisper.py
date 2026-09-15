"""GET /system/status must report the transcription config actually in use.

The endpoint read WHISPER_MODEL and WHISPER_DEVICE from the environment, so an
instance transcribing on a remote OpenAI-compatible API still advertised a
local model on cuda. The DB settings win, the same way GET /settings resolves
them.
"""
import pytest

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('system_status_whisper_test_')

from api import get_database  # noqa: E402

WHISPER_KEYS = (
    'whisper_backend', 'whisper_model', 'whisper_api_model', 'whisper_api_base_url',
)


@pytest.fixture
def db():
    database = get_database()
    saved = {key: database.get_setting(key) for key in WHISPER_KEYS}
    yield database
    for key, value in saved.items():
        database.set_setting(key, value if value is not None else '')


def _status_settings(client):
    response = client.get('/api/v1/system/status')
    assert response.status_code == 200
    return response.get_json()['settings']


def test_remote_backend_reports_api_model_and_host(app_client, db, monkeypatch):
    monkeypatch.setenv('WHISPER_MODEL', 'small')
    monkeypatch.setenv('WHISPER_DEVICE', 'cuda')
    db.set_setting('whisper_backend', 'openai-api')
    db.set_setting('whisper_model', 'small')
    db.set_setting('whisper_api_model', 'large-v3')
    db.set_setting('whisper_api_base_url', 'https://whisper.example.com/v1')

    settings = _status_settings(app_client)

    assert settings['whisperBackend'] == 'openai-api'
    assert settings['whisperModel'] == 'large-v3'
    assert settings['whisperDevice'] == 'remote'
    assert settings['whisperApiHost'] == 'https://whisper.example.com'


def test_remote_host_drops_credentials(app_client, db, monkeypatch):
    monkeypatch.setenv('WHISPER_API_BASE_URL', '')
    db.set_setting('whisper_backend', 'openai-api')
    db.set_setting('whisper_api_base_url', 'https://user:secret@whisper.example.com/v1?token=abc')

    settings = _status_settings(app_client)

    assert settings['whisperApiHost'] == 'https://whisper.example.com'


def test_local_backend_reports_db_model_over_env(app_client, db, monkeypatch):
    monkeypatch.setenv('WHISPER_MODEL', 'small')
    monkeypatch.setenv('WHISPER_DEVICE', 'cpu')
    db.set_setting('whisper_backend', 'local')
    db.set_setting('whisper_model', 'medium')
    db.set_setting('whisper_api_model', 'large-v3')

    settings = _status_settings(app_client)

    assert settings['whisperBackend'] == 'local'
    assert settings['whisperModel'] == 'medium'
    assert settings['whisperDevice'] == 'cpu'
    assert settings['whisperApiHost'] is None


def test_unset_backend_falls_back_to_env_default(app_client, db, monkeypatch):
    monkeypatch.setenv('WHISPER_BACKEND', 'openai-api')
    monkeypatch.setenv('WHISPER_API_MODEL', 'whisper-1')
    db.set_setting('whisper_backend', '')
    db.set_setting('whisper_api_model', '')
    db.set_setting('whisper_api_base_url', '')

    settings = _status_settings(app_client)

    assert settings['whisperBackend'] == 'openai-api'
    assert settings['whisperModel'] == 'whisper-1'
    assert settings['whisperApiHost'] is None
