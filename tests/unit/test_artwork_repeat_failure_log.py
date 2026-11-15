"""A non-image artwork URL is rejected unsaved and warns once."""
import logging
from unittest.mock import MagicMock, patch


def _html_response():
    response = MagicMock()
    response.headers = {'Content-Type': 'text/html; charset=utf-8'}
    response.raise_for_status = lambda: None
    return response


def test_non_image_response_is_rejected_unsaved_and_warns_once(storage, caplog):
    url = 'https://example.com/page'
    storage.db.create_podcast('example-feed', 'https://example.com/feed.xml')
    with patch('utils.safe_http.safe_get', return_value=_html_response()):
        with caplog.at_level(logging.DEBUG, logger='storage'):
            assert storage.download_artwork('example-feed', url) is False
            storage._artwork_failure_cache.clear()
            assert storage.download_artwork('example-feed', url, bypass_backoff=True) is False
    rejected = [r for r in caplog.records if 'artwork_rejected_content_type' in r.getMessage()]
    assert [r.levelno for r in rejected] == [logging.WARNING, logging.DEBUG]
    assert storage.get_artwork('example-feed') is None


EPISODE = 'a1b2c3d4e5f6'
EPISODE_URL = 'https://example.com/cover.jpg'


def _jpeg_response():
    response = _html_response()
    response.headers = {'Content-Type': 'image/jpeg'}
    response.iter_content = lambda chunk_size: iter([b'\xff\xd8\xff\xe0' + b'\x00' * 64])
    return response


def _episode_levels(storage, caplog, response, repeats=1):
    """Levels of the rejection logs over repeats, with the 6 hour failure cache expired between them."""
    levels = []
    with patch('utils.safe_http.safe_get', return_value=response):
        for _ in range(repeats):
            caplog.clear()
            storage._artwork_failure_cache.clear()
            storage.download_episode_artwork('example-feed', EPISODE, EPISODE_URL)
            levels += [r.levelno for r in caplog.records if 'episode_artwork_rejected' in r.getMessage()]
    return levels


def test_episode_cover_warns_once_then_logs_repeats_at_debug(storage, caplog):
    storage.db.create_podcast('example-feed', 'https://example.com/feed.xml')
    with caplog.at_level(logging.DEBUG, logger='storage'):
        assert _episode_levels(storage, caplog, _html_response(), repeats=3) == [
            logging.WARNING, logging.DEBUG, logging.DEBUG]


def test_episode_cover_warns_again_after_a_success(storage, caplog):
    storage.db.create_podcast('example-feed', 'https://example.com/feed.xml')
    with caplog.at_level(logging.DEBUG, logger='storage'):
        assert _episode_levels(storage, caplog, _html_response(), repeats=2) == [logging.WARNING, logging.DEBUG]
        assert _episode_levels(storage, caplog, _jpeg_response()) == []
        assert storage.has_episode_artwork('example-feed', EPISODE)
        assert _episode_levels(storage, caplog, _html_response()) == [logging.WARNING]
