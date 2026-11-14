"""A 403 on the download User-Agent retries artwork once with the feed User-Agent."""
import logging
import os
import sys
from unittest.mock import MagicMock, patch

import pytest
from requests import HTTPError

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from storage import Storage  # noqa: E402

URL = 'https://example.com/cover.jpg'
JPEG = b'\xff\xd8\xff\xe0' + b'\x00' * 20


@pytest.fixture
def storage(temp_db, tmp_path):
    Storage._instance = None
    s = Storage(str(tmp_path))
    s.db.create_podcast('example-podcast', 'https://example.com/feed.xml')
    yield s
    Storage._instance = None


@pytest.fixture
def agents(monkeypatch):
    monkeypatch.setattr('storage.download_user_agent', lambda podcast=None: 'Download/1.0')
    monkeypatch.setattr('storage.feed_user_agent', lambda podcast=None: 'Feed/2.0')


def _resp(status, agent):
    response = MagicMock()
    response.status_code = status
    response.headers = {'Content-Type': 'image/jpeg'}
    response.request.headers = {'User-Agent': agent}
    response.iter_content = lambda chunk_size: iter([JPEG])
    if status >= 400:
        response.raise_for_status = MagicMock(side_effect=HTTPError(response=response))
    else:
        response.raise_for_status = lambda: None
    return response


def _fetcher(statuses):
    seen = []

    def fetch(*args, **kwargs):
        agent = kwargs['headers']['User-Agent']
        seen.append(agent)
        return _resp(statuses[agent], agent)
    return fetch, seen


def test_403_then_feed_agent_success_stores_artwork(storage, agents, caplog):
    fetch, seen = _fetcher({'Download/1.0': 403, 'Feed/2.0': 200})
    with patch('utils.safe_http.safe_get', side_effect=fetch):
        with caplog.at_level(logging.DEBUG, logger='storage'):
            assert storage.download_artwork('example-podcast', URL) is True
    assert seen == ['Download/1.0', 'Feed/2.0']
    assert storage.get_artwork('example-podcast') is not None
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert any('accepts the feed User-Agent' in r.getMessage() for r in caplog.records)


def test_403_with_both_agents_records_one_failure_and_one_warning(storage, agents, caplog):
    fetch, seen = _fetcher({'Download/1.0': 403, 'Feed/2.0': 403})
    with patch('utils.safe_http.safe_get', side_effect=fetch):
        with caplog.at_level(logging.DEBUG, logger='storage'):
            assert storage.download_artwork('example-podcast', URL) is False
    assert seen == ['Download/1.0', 'Feed/2.0']
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 1
    podcast = storage.db.get_podcast_by_slug('example-podcast')
    assert list(storage._artwork_failure_state(podcast)) == [URL]


def test_404_does_not_trigger_the_fallback(storage, agents):
    fetch, seen = _fetcher({'Download/1.0': 404, 'Feed/2.0': 200})
    with patch('utils.safe_http.safe_get', side_effect=fetch):
        assert storage.download_artwork('example-podcast', URL) is False
    assert seen == ['Download/1.0']


def test_first_success_makes_one_request(storage, agents):
    fetch, seen = _fetcher({'Download/1.0': 200, 'Feed/2.0': 200})
    with patch('utils.safe_http.safe_get', side_effect=fetch):
        assert storage.download_artwork('example-podcast', URL) is True
    assert seen == ['Download/1.0']


def test_feed_override_is_the_fallback_agent(storage, monkeypatch):
    import user_agent
    monkeypatch.setattr(user_agent, '_resolve', lambda key: 'Global/' + key)
    storage.db.update_podcast('example-podcast', feed_user_agent_override='Override/3.0')
    fetch, seen = _fetcher({'Global/download_user_agent': 403, 'Override/3.0': 200})
    with patch('utils.safe_http.safe_get', side_effect=fetch):
        assert storage.download_artwork('example-podcast', URL) is True
    assert seen == ['Global/download_user_agent', 'Override/3.0']


def test_episode_artwork_gets_the_same_fallback(storage, agents):
    fetch, seen = _fetcher({'Download/1.0': 403, 'Feed/2.0': 200})
    with patch('utils.safe_http.safe_get', side_effect=fetch):
        assert storage.download_episode_artwork('example-podcast', 'a1b2c3d4e5f6', URL) is True
    assert seen == ['Download/1.0', 'Feed/2.0']
