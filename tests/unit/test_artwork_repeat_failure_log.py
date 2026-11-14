"""A non-image artwork URL is rejected unsaved and warns once."""
import logging
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from storage import Storage  # noqa: E402


@pytest.fixture
def storage(temp_db, tmp_path):
    Storage._instance = None
    s = Storage(str(tmp_path))
    yield s
    Storage._instance = None


def _html_response():
    response = MagicMock()
    response.headers = {'Content-Type': 'text/html; charset=utf-8'}
    response.raise_for_status = lambda: None
    return response


def test_non_image_response_is_rejected_unsaved_and_warns_once(storage, caplog):
    url = 'https://example.com/page'
    storage.db.create_podcast('example-feed', 'https://example.com/feed.xml')
    with patch('storage.safe_get', return_value=_html_response()):
        with caplog.at_level(logging.DEBUG, logger='storage'):
            assert storage.download_artwork('example-feed', url) is False
            storage._artwork_failure_cache.clear()
            assert storage.download_artwork('example-feed', url, bypass_backoff=True) is False
    rejected = [r for r in caplog.records if 'artwork_rejected_content_type' in r.getMessage()]
    assert [r.levelno for r in rejected] == [logging.WARNING, logging.DEBUG]
    assert storage.get_artwork('example-feed') is None
