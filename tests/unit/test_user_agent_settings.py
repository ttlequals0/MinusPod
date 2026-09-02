"""Tests for the configurable outbound User-Agent strings."""

import os
import pathlib

import pytest

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('user_agent_test_')
import user_agent
from config import (
    APP_USER_AGENT, BROWSER_USER_AGENT, USER_AGENT_MAX_LENGTH,
    validate_user_agent,
)
from database import Database


@pytest.fixture(autouse=True)
def clean_ua_settings():
    """Each test starts with no stored UA and an empty resolver cache."""
    db = Database()
    for key in (user_agent.DOWNLOAD_UA_SETTING, user_agent.FEED_UA_SETTING):
        db.clear_setting(key)
    user_agent.invalidate_cache()
    yield
    for key in (user_agent.DOWNLOAD_UA_SETTING, user_agent.FEED_UA_SETTING):
        db.clear_setting(key)
    user_agent.invalidate_cache()


@pytest.mark.parametrize("value, valid", [
    ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/153.0.0.0', True),
    ('PodcastAdRemover/1.0', True),
    ('', False),
    ('   ', False),
    # Header injection: a UA carrying CR/LF could forge additional headers.
    ('Mozilla/5.0\r\nX-Injected: yes', False),
    ('Mozilla/5.0\nX-Injected: yes', False),
    # Non-ASCII cannot be sent in a header without encoding games.
    ('Mozilla/5.0 café', False),
    ('x' * (USER_AGENT_MAX_LENGTH + 1), False),
    ('x' * USER_AGENT_MAX_LENGTH, True),
])
def test_validate_user_agent(value, valid):
    assert validate_user_agent(value) is valid


def test_resolvers_fall_back_to_the_config_defaults():
    assert user_agent.download_user_agent() == BROWSER_USER_AGENT
    assert user_agent.feed_user_agent() == APP_USER_AGENT


def test_stored_setting_overrides_the_default():
    Database().set_setting(user_agent.DOWNLOAD_UA_SETTING, 'CustomAgent/9.9')
    user_agent.invalidate_cache()
    assert user_agent.download_user_agent() == 'CustomAgent/9.9'
    # The two strings are independent.
    assert user_agent.feed_user_agent() == APP_USER_AGENT


def test_stored_setting_is_stripped():
    Database().set_setting(user_agent.DOWNLOAD_UA_SETTING, '  CustomAgent/9.9  ')
    user_agent.invalidate_cache()
    assert user_agent.download_user_agent() == 'CustomAgent/9.9'


def test_invalid_stored_setting_falls_back_to_the_default():
    """A row written outside the API (direct DB edit) must not be able to
    inject a header or send a value the transport will reject."""
    Database().set_setting(user_agent.DOWNLOAD_UA_SETTING, 'Bad/1.0\r\nX-Injected: yes')
    user_agent.invalidate_cache()
    assert user_agent.download_user_agent() == BROWSER_USER_AGENT


def test_default_download_agent_is_a_current_browser_string():
    """The default ages out: hosts that gate on a browser version floor start
    refusing older strings. Guards against silently reverting to a stale one."""
    assert 'Chrome/' in BROWSER_USER_AGENT
    version = int(BROWSER_USER_AGENT.split('Chrome/')[1].split('.')[0])
    assert version >= 140


def _probe():
    """A Transcriber built without loading a model: the availability probe is
    pure HTTP and touches none of the transcription state."""
    from transcriber import Transcriber
    return Transcriber.__new__(Transcriber)


class TestAudioAvailabilityProbe:
    """check_audio_availability distinguishes a refusal from a not-yet-ready
    file, because only one of them is worth retrying."""

    @pytest.mark.parametrize("status_code, available, message", [
        (200, True, None),
        (403, False, 'CDN refused the request (403)'),
        (404, False, 'CDN not ready (404)'),
        (503, False, 'CDN server error (503)'),
    ])
    def test_status_code_mapping(self, monkeypatch, status_code, available, message):
        class FakeResponse:
            def __init__(self, code):
                self.status_code = code

        monkeypatch.setattr(
            'utils.safe_http.safe_head',
            lambda *a, **k: FakeResponse(status_code))
        assert _probe().check_audio_availability(
            'https://cdn.example/media.mp3') == (available, message)

    def test_probe_sends_the_configured_user_agent(self, monkeypatch):
        Database().set_setting(user_agent.DOWNLOAD_UA_SETTING, 'CustomAgent/9.9')
        user_agent.invalidate_cache()
        sent = {}

        class FakeResponse:
            status_code = 200

        def fake_head(url, **kwargs):
            sent.update(kwargs.get('headers') or {})
            return FakeResponse()

        monkeypatch.setattr('utils.safe_http.safe_head', fake_head)
        _probe().check_audio_availability('https://cdn.example/media.mp3')
        assert sent['User-Agent'] == 'CustomAgent/9.9'


def test_module_imports_before_storage():
    """user_agent must stay a leaf: storage imports it, so importing it first
    has to work. `utils.ttl_cache` drags utils/__init__ -> utils.audio ->
    storage and puts it back in the cycle."""
    import subprocess
    import sys

    src = str(pathlib.Path(__file__).resolve().parents[2] / 'src')
    result = subprocess.run(
        [sys.executable, '-c', 'import user_agent; print(user_agent.feed_user_agent())'],
        capture_output=True, text=True, env={'PYTHONPATH': src, 'PATH': os.environ['PATH']})
    assert result.returncode == 0, result.stderr
