"""Unit tests for HEAD request handling on serve_episode route.

HEAD requests on unprocessed episodes should NOT trigger JIT processing.
They should proxy upstream audio headers instead.
"""
import os
import sys
import tempfile
import shutil
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock

import pytest
import requests.exceptions

# Create temp data dir and set env before any imports that touch /app/data
_test_data_dir = tempfile.mkdtemp(prefix='head_test_')
os.environ['SECRET_KEY'] = 'test-secret'
os.environ['DATA_DIR'] = _test_data_dir

# Patch Database and Storage defaults before importing main_app
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

import database
import storage as storage_mod
database.Database._instance = None
database.Database.__init__.__defaults__ = (_test_data_dir,)
database.Database.__new__.__defaults__ = (_test_data_dir,)
storage_mod.Storage.__init__.__defaults__ = (_test_data_dir,)

from main_app import app
from main_app.routes import _head_upstream, _lookup_episode


@pytest.fixture
def client():
    """Flask test client."""
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c


@pytest.fixture
def feed_map():
    return {
        'test-pod': {
            'in': 'https://example.com/feed.xml',
            'out': 'test-pod',
        }
    }


class TestHeadRequestDoesNotProcess:
    """HEAD requests on unprocessed episodes must not trigger processing."""

    @patch('main_app.processing.start_background_processing')
    @patch('main_app.routes._head_upstream')
    @patch('main_app.routes._lookup_episode', return_value=({'id': 'abc123', 'url': 'https://example.com/ep.mp3', 'title': 'Ep 1', 'description': 'desc', 'artwork_url': None}, 'Test Podcast'))
    @patch('main_app.db')
    @patch('main_app.routes.get_feed_map')
    def test_head_unprocessed_proxies_upstream(
        self, mock_feed_map, mock_db, mock_lookup, mock_head, mock_start,
        client, feed_map,
    ):
        mock_feed_map.return_value = feed_map
        mock_db.get_episode.return_value = None
        from flask import Response
        mock_head.return_value = Response('', status=200, headers={
            'Content-Type': 'audio/mpeg',
            'Content-Length': '12345678',
        })

        resp = client.head('/episodes/test-pod/abc123.mp3')

        assert resp.status_code == 200
        mock_head.assert_called_once_with('test-pod', 'abc123', 'https://example.com/ep.mp3')
        mock_start.assert_not_called()

    @patch('main_app.processing.start_background_processing')
    @patch('main_app.routes._head_upstream')
    @patch('main_app.routes._lookup_episode', return_value=({'id': 'abc123', 'url': 'https://example.com/ep.mp3', 'title': 'Ep 1', 'description': 'desc', 'artwork_url': None}, 'Test Podcast'))
    @patch('main_app.db')
    @patch('main_app.routes.get_feed_map')
    def test_head_failed_episode_proxies_upstream(
        self, mock_feed_map, mock_db, mock_lookup, mock_head, mock_start,
        client, feed_map,
    ):
        mock_feed_map.return_value = feed_map
        mock_db.get_episode.return_value = {'status': 'failed', 'retry_count': 1}
        from flask import Response
        mock_head.return_value = Response('', status=200, headers={
            'Content-Type': 'audio/mpeg',
        })

        resp = client.head('/episodes/test-pod/abc123.mp3')

        assert resp.status_code == 200
        mock_start.assert_not_called()

    @patch('main_app.processing.start_background_processing')
    @patch('main_app.routes._lookup_episode', return_value=(None, None))
    @patch('main_app.db')
    @patch('main_app.routes.get_feed_map')
    def test_head_unprocessed_404_when_not_in_rss(
        self, mock_feed_map, mock_db, mock_lookup, mock_start,
        client, feed_map,
    ):
        mock_feed_map.return_value = feed_map
        mock_db.get_episode.return_value = None

        resp = client.head('/episodes/test-pod/abc123.mp3')

        assert resp.status_code == 404
        mock_start.assert_not_called()


class TestHeadRequestProcessedEpisode:
    """HEAD requests on processed episodes should serve the local file normally."""

    @patch('main_app.storage')
    @patch('main_app.db')
    @patch('main_app.routes.get_feed_map')
    def test_head_processed_serves_local_file(
        self, mock_feed_map, mock_db, mock_storage,
        client, feed_map, tmp_path,
    ):
        mock_feed_map.return_value = feed_map
        mock_db.get_episode.return_value = {'status': 'processed'}

        # Create a fake audio file
        fake_mp3 = tmp_path / 'episode.mp3'
        fake_mp3.write_bytes(b'\xff\xfb\x90\x00' * 10)
        mock_storage.get_episode_path.return_value = fake_mp3

        resp = client.head('/episodes/test-pod/abc123.mp3')

        assert resp.status_code == 200
        assert resp.content_length > 0


class TestGetRequestStillProcesses:
    """GET requests should still trigger JIT processing as before."""

    @patch('main_app.status_service')
    @patch('main_app.processing.start_background_processing', return_value=(True, None))
    @patch('main_app.routes._lookup_episode', return_value=({'id': 'abc123', 'url': 'https://example.com/ep.mp3', 'title': 'Ep 1', 'description': 'desc', 'artwork_url': None}, 'Test Podcast'))
    @patch('main_app.db')
    @patch('main_app.routes.get_feed_map')
    def test_get_unprocessed_triggers_processing(
        self, mock_feed_map, mock_db, mock_lookup, mock_start,
        mock_status, client, feed_map,
    ):
        mock_feed_map.return_value = feed_map
        mock_db.get_episode.return_value = None

        resp = client.get('/episodes/test-pod/abc123.mp3')

        assert resp.status_code == 503
        mock_start.assert_called_once()


class TestHeadUpstreamHelper:
    """Test _head_upstream helper directly."""

    @patch('main_app.routes.safe_head')
    def test_proxies_content_headers(self, mock_head):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {
            'Content-Type': 'audio/mpeg',
            'Content-Length': '98765432',
            'Accept-Ranges': 'bytes',
            'X-Other': 'ignored',
        }
        mock_head.return_value = mock_resp

        with app.app_context():
            resp = _head_upstream('slug', 'ep1', 'https://example.com/audio.mp3')

        assert resp.status_code == 200
        assert resp.headers['Content-Type'] == 'audio/mpeg'
        assert resp.headers['Content-Length'] == '98765432'
        assert resp.headers['Accept-Ranges'] == 'bytes'
        assert 'X-Other' not in resp.headers

    @patch('main_app.routes.safe_head', side_effect=requests.exceptions.ConnectionError('timeout'))
    def test_returns_503_on_upstream_failure(self, mock_head):
        from werkzeug.exceptions import ServiceUnavailable

        with app.test_request_context():
            with pytest.raises(ServiceUnavailable):
                _head_upstream('slug', 'ep1', 'https://example.com/audio.mp3')


class TestLookupEpisode:
    """Test _lookup_episode helper."""

    @patch('main_app.rss_parser')
    def test_returns_episode_and_podcast_name(self, mock_rss):
        mock_rss.fetch_feed.return_value = '<rss></rss>'
        mock_parsed = MagicMock()
        mock_parsed.feed.get.return_value = 'My Podcast'
        mock_rss.parse_feed.return_value = mock_parsed
        mock_rss.extract_episodes.return_value = [
            {'id': 'ep1', 'url': 'https://example.com/ep1.mp3', 'title': 'Ep 1'},
            {'id': 'ep2', 'url': 'https://example.com/ep2.mp3', 'title': 'Ep 2'},
        ]

        feed_map = {'pod': {'in': 'https://example.com/feed.xml'}}
        ep_data, podcast_name = _lookup_episode('pod', 'ep2', feed_map)

        assert ep_data['url'] == 'https://example.com/ep2.mp3'
        assert ep_data['id'] == 'ep2'
        assert podcast_name == 'My Podcast'

    @patch('main_app.rss_parser')
    def test_returns_none_tuple_when_not_found(self, mock_rss):
        mock_rss.fetch_feed.return_value = '<rss></rss>'
        mock_parsed = MagicMock()
        mock_parsed.feed.get.return_value = 'My Podcast'
        mock_rss.parse_feed.return_value = mock_parsed
        mock_rss.extract_episodes.return_value = [
            {'id': 'ep1', 'url': 'https://example.com/ep1.mp3'},
        ]

        feed_map = {'pod': {'in': 'https://example.com/feed.xml'}}
        ep_data, podcast_name = _lookup_episode('pod', 'missing', feed_map)

        assert ep_data is None
        assert podcast_name is None

    @patch('main_app.rss_parser')
    def test_returns_none_tuple_when_feed_unavailable(self, mock_rss):
        mock_rss.fetch_feed.return_value = None

        feed_map = {'pod': {'in': 'https://example.com/feed.xml'}}
        ep_data, podcast_name = _lookup_episode('pod', 'ep1', feed_map)

        assert ep_data is None
        assert podcast_name is None


class TestJITRetryCooldown:
    """JIT route should respect cooldown between retries for failed episodes."""

    def _make_failed_episode(self, seconds_ago, retry_count):
        """Build a failed episode dict with updated_at N seconds in the past."""
        ts = (datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)).isoformat()
        return {'status': 'failed', 'retry_count': retry_count, 'updated_at': ts}

    @patch('main_app.processing.start_background_processing', return_value=(True, None))
    @patch('main_app.routes._lookup_episode', return_value=({'id': 'abc123', 'url': 'https://example.com/ep.mp3', 'title': 'Ep 1', 'description': 'desc', 'artwork_url': None}, 'Test Podcast'))
    @patch('main_app.status_service')
    @patch('main_app.db')
    @patch('main_app.routes.get_feed_map')
    def test_failed_episode_within_cooldown_returns_503(
        self, mock_feed_map, mock_db, mock_status, mock_lookup, mock_start,
        client, feed_map,
    ):
        """Episode that failed <60s ago should return 503 with Retry-After."""
        mock_feed_map.return_value = feed_map
        mock_db.get_episode.return_value = self._make_failed_episode(10, retry_count=1)

        resp = client.get('/episodes/test-pod/abc123.mp3')

        assert resp.status_code == 503
        assert 'Retry-After' in resp.headers
        assert int(resp.headers['Retry-After']) >= 30
        mock_start.assert_not_called()

    @patch('main_app.processing.start_background_processing', return_value=(True, None))
    @patch('main_app.routes._lookup_episode', return_value=({'id': 'abc123', 'url': 'https://example.com/ep.mp3', 'title': 'Ep 1', 'description': 'desc', 'artwork_url': None}, 'Test Podcast'))
    @patch('main_app.status_service')
    @patch('main_app.db')
    @patch('main_app.routes.get_feed_map')
    def test_failed_episode_past_cooldown_retries(
        self, mock_feed_map, mock_db, mock_status, mock_lookup, mock_start,
        client, feed_map,
    ):
        """Episode that failed >60s ago should proceed to retry."""
        mock_feed_map.return_value = feed_map
        mock_db.get_episode.return_value = self._make_failed_episode(120, retry_count=1)

        resp = client.get('/episodes/test-pod/abc123.mp3')

        # Should proceed to processing (503 from start_background_processing)
        assert resp.status_code == 503
        mock_start.assert_called_once()

    @patch('main_app.processing.start_background_processing', return_value=(True, None))
    @patch('main_app.routes._lookup_episode', return_value=({'id': 'abc123', 'url': 'https://example.com/ep.mp3', 'title': 'Ep 1', 'description': 'desc', 'artwork_url': None}, 'Test Podcast'))
    @patch('main_app.status_service')
    @patch('main_app.db')
    @patch('main_app.routes.get_feed_map')
    def test_cooldown_doubles_per_retry(
        self, mock_feed_map, mock_db, mock_status, mock_lookup, mock_start,
        client, feed_map,
    ):
        """Retry 2 should require 120s cooldown (60 * 2^1)."""
        mock_feed_map.return_value = feed_map
        # 90s ago - past 60s cooldown but within 120s cooldown for retry 2
        mock_db.get_episode.return_value = self._make_failed_episode(90, retry_count=2)

        resp = client.get('/episodes/test-pod/abc123.mp3')

        assert resp.status_code == 503
        assert 'Retry-After' in resp.headers
        mock_start.assert_not_called()

    @patch('main_app.processing.start_background_processing', return_value=(True, None))
    @patch('main_app.routes._lookup_episode', return_value=({'id': 'abc123', 'url': 'https://example.com/ep.mp3', 'title': 'Ep 1', 'description': 'desc', 'artwork_url': None}, 'Test Podcast'))
    @patch('main_app.status_service')
    @patch('main_app.db')
    @patch('main_app.routes.get_feed_map')
    def test_first_failure_no_cooldown(
        self, mock_feed_map, mock_db, mock_status, mock_lookup, mock_start,
        client, feed_map,
    ):
        """retry_count=0 should skip cooldown (first attempt just failed)."""
        mock_feed_map.return_value = feed_map
        mock_db.get_episode.return_value = self._make_failed_episode(5, retry_count=0)

        resp = client.get('/episodes/test-pod/abc123.mp3')

        # retry_count=0 skips cooldown, proceeds to processing
        assert resp.status_code == 503
        mock_start.assert_called_once()


def teardown_module():
    """Clean up temp directory."""
    shutil.rmtree(_test_data_dir, ignore_errors=True)
