"""A download 403 is probed with the other User-Agent before it is classified."""
import logging

import pytest

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('cdn_403_test_')

import main_app.processing as processing  # noqa: E402
from main_app.pipeline import transcribe
from main_app.pipeline.transcribe import CDN_BLOCKED_MESSAGE, _download_episode_audio
from main_app.pipeline.common import is_transient_error
from main_app.pipeline import signals
from utils.log_format import QueryStringRedactingFilter, make_formatter

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
    monkeypatch.setattr(transcribe, 'download_user_agent', lambda podcast=None: (podcast or {}).get('download_user_agent_override') or 'Browser/1')
    monkeypatch.setattr(signals, 'download_user_agent', lambda podcast=None: (podcast or {}).get('download_user_agent_override') or 'Browser/1')
    monkeypatch.setattr(transcribe, 'feed_user_agent', lambda podcast=None: 'Podcaster/1')


def test_user_agent_floor_downloads_with_the_accepted_string(monkeypatch, agents, caplog):
    fake = FakeTranscriber({'Browser/1': (False, 'CDN refused the request (403)'),
                            'Podcaster/1': (True, None)})
    monkeypatch.setattr(processing, 'transcriber', fake)
    with caplog.at_level(logging.WARNING):
        assert _download_episode_audio(URL) == '/tmp/audio.mp3'
    assert fake.downloaded_with == ['Podcaster/1']
    assert "refuses the download User-Agent 'Browser/1'" in caplog.text
    assert "accepts the feed User-Agent 'Podcaster/1'" in caplog.text


def test_block_on_both_agents_is_transient(monkeypatch, agents):
    fake = FakeTranscriber({'Browser/1': (False, 'CDN refused the request (403)'),
                            'Podcaster/1': (False, 'CDN refused the request (403)')})
    monkeypatch.setattr(processing, 'transcriber', fake)
    with pytest.raises(Exception) as exc:
        _download_episode_audio(URL)
    assert str(exc.value) == CDN_BLOCKED_MESSAGE
    assert is_transient_error(exc.value) is True
    assert fake.downloaded_with == []


def test_other_probe_failures_do_not_reprobe(monkeypatch, agents):
    fake = FakeTranscriber({'Browser/1': (False, 'CDN not ready (404)')})
    monkeypatch.setattr(processing, 'transcriber', fake)
    with pytest.raises(Exception) as exc:
        _download_episode_audio(URL)
    assert str(exc.value) == 'CDN not ready (404)'


def test_feed_override_and_accepted_fallback_are_recorded(monkeypatch, agents):
    fake = FakeTranscriber({'Feed/2': (False, 'CDN refused the request (403)'),
                            'Podcaster/1': (True, None)})
    monkeypatch.setattr(processing, 'transcriber', fake)
    outcome = {}
    assert _download_episode_audio(URL, {'download_user_agent_override': 'Feed/2'}, outcome) == '/tmp/audio.mp3'
    assert outcome == {'download_user_agent': 'Feed/2', 'fallback_user_agent': 'Podcaster/1'}
    assert fake.downloaded_with == ['Podcaster/1']


@pytest.mark.parametrize("opt_in", [True, False])
def test_pipeline_download_lines_follow_the_query_opt_in(monkeypatch, agents, caplog, opt_in):
    monkeypatch.setattr(transcribe, 'log_download_query_enabled', lambda: opt_in)
    fake = FakeTranscriber({'Browser/1': (False, 'CDN refused the request (403)'),
                            'Podcaster/1': (False, 'CDN not ready (404)')})
    monkeypatch.setattr(processing, 'transcriber', fake)
    with caplog.at_level(logging.WARNING), pytest.raises(Exception):
        _download_episode_audio('https://u:PASS@cdn.example/media.mp3?token=SIGNED')
    for record in caplog.records:
        QueryStringRedactingFilter().filter(record)
    out = '\n'.join(make_formatter(fmt).format(r) for r in caplog.records for fmt in ('text', 'json'))
    assert ('token=SIGNED' in out) is opt_in
    assert 'PASS' not in out
