"""A download 403 is probed with the other User-Agent before it is classified."""
import logging

import pytest

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('cdn_403_test_')

import main_app.processing as processing  # noqa: E402
from main_app.processing import (  # noqa: E402
    CDN_BLOCKED_MESSAGE, _download_episode_audio, is_transient_error,
)

URL = 'https://cdn.example/media.mp3'


class FakeTranscriber:
    def __init__(self, by_agent):
        self.by_agent = by_agent
        self.downloaded_with = []

    def check_audio_availability(self, url, timeout=10, user_agent=None):
        return self.by_agent[user_agent]

    def download_audio(self, url, timeout=(10, 300), user_agent=None):
        self.downloaded_with.append(user_agent)
        return '/tmp/audio.mp3'


@pytest.fixture
def agents(monkeypatch):
    monkeypatch.setattr(processing, 'download_user_agent', lambda: 'Browser/1')
    monkeypatch.setattr(processing, 'feed_user_agent', lambda: 'Podcaster/1')


def test_user_agent_floor_downloads_with_the_accepted_string(monkeypatch, agents, caplog):
    fake = FakeTranscriber({None: (False, 'CDN refused the request (403)'),
                            'Podcaster/1': (True, None)})
    monkeypatch.setattr(processing, 'transcriber', fake)
    with caplog.at_level(logging.WARNING):
        assert _download_episode_audio(URL) == '/tmp/audio.mp3'
    assert fake.downloaded_with == ['Podcaster/1']
    assert "refuses the download User-Agent 'Browser/1'" in caplog.text
    assert "accepts the feed User-Agent 'Podcaster/1'" in caplog.text


def test_block_on_both_agents_is_transient(monkeypatch, agents):
    fake = FakeTranscriber({None: (False, 'CDN refused the request (403)'),
                            'Podcaster/1': (False, 'CDN refused the request (403)')})
    monkeypatch.setattr(processing, 'transcriber', fake)
    with pytest.raises(Exception) as exc:
        _download_episode_audio(URL)
    assert str(exc.value) == CDN_BLOCKED_MESSAGE
    assert is_transient_error(exc.value) is True
    assert fake.downloaded_with == []


def test_other_probe_failures_do_not_reprobe(monkeypatch, agents):
    fake = FakeTranscriber({None: (False, 'CDN not ready (404)')})
    monkeypatch.setattr(processing, 'transcriber', fake)
    with pytest.raises(Exception) as exc:
        _download_episode_audio(URL)
    assert str(exc.value) == 'CDN not ready (404)'
