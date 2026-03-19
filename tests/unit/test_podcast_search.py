"""Unit tests for podcast search endpoint."""
import json
import os
import sys
import tempfile
from unittest.mock import patch, MagicMock

import pytest

# Create temp data dir and set env before any imports that touch /app/data
_test_data_dir = tempfile.mkdtemp(prefix='podcast_search_test_')
os.environ['SECRET_KEY'] = 'test-secret'
os.environ['DATA_DIR'] = _test_data_dir

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

import database
import storage as storage_mod
database.Database._instance = None
database.Database.__init__.__defaults__ = (_test_data_dir,)
database.Database.__new__.__defaults__ = (_test_data_dir,)
storage_mod.Storage.__init__.__defaults__ = (_test_data_dir,)

from main_app import app


@pytest.fixture
def client():
    """Flask test client."""
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c


class TestPodcastSearchValidation:
    """Tests for query parameter validation."""

    def test_missing_query_returns_400(self, client):
        response = client.get('/api/v1/podcast-search')
        assert response.status_code == 400
        data = json.loads(response.data)
        assert 'required' in data['error'].lower()

    def test_empty_query_returns_400(self, client):
        response = client.get('/api/v1/podcast-search?q=')
        assert response.status_code == 400

    def test_whitespace_only_query_returns_400(self, client):
        response = client.get('/api/v1/podcast-search?q=%20%20')
        assert response.status_code == 400


class TestPodcastSearchCredentials:
    """Tests for credential resolution."""

    def test_no_credentials_returns_503(self, client):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('PODCAST_INDEX_API_KEY', None)
            os.environ.pop('PODCAST_INDEX_API_SECRET', None)
            response = client.get('/api/v1/podcast-search?q=test')
        assert response.status_code == 503
        data = json.loads(response.data)
        assert 'credentials' in data['error'].lower()

    def test_key_without_secret_returns_503(self, client):
        with patch.dict(os.environ, {'PODCAST_INDEX_API_KEY': 'key'}, clear=False):
            os.environ.pop('PODCAST_INDEX_API_SECRET', None)
            response = client.get('/api/v1/podcast-search?q=test')
        assert response.status_code == 503


class TestPodcastSearchAPICall:
    """Tests for PodcastIndex API interaction."""

    @patch('api.podcast_search.requests.get')
    @patch('api.podcast_search._get_podcast_index_credentials', return_value=('key', 'secret'))
    def test_successful_search(self, mock_creds, mock_get, client):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            'feeds': [
                {
                    'id': 123,
                    'title': 'Test Podcast',
                    'description': 'A test',
                    'artwork': 'https://example.com/art.png',
                    'url': 'https://example.com/feed.xml',
                    'author': 'Author',
                    'link': 'https://example.com',
                }
            ]
        }
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        response = client.get('/api/v1/podcast-search?q=test')
        assert response.status_code == 200
        data = json.loads(response.data)
        assert len(data['results']) == 1
        assert data['results'][0]['title'] == 'Test Podcast'
        assert data['results'][0]['feedUrl'] == 'https://example.com/feed.xml'
        assert data['results'][0]['artworkUrl'] == 'https://example.com/art.png'

    @patch('api.podcast_search.requests.get')
    @patch('api.podcast_search._get_podcast_index_credentials', return_value=('key', 'secret'))
    def test_artwork_fallback_to_image(self, mock_creds, mock_get, client):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            'feeds': [{'id': 1, 'title': 'P', 'artwork': '', 'image': 'https://img.png', 'url': ''}]
        }
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        response = client.get('/api/v1/podcast-search?q=test')
        data = json.loads(response.data)
        assert data['results'][0]['artworkUrl'] == 'https://img.png'

    @patch('api.podcast_search.requests.get')
    @patch('api.podcast_search._get_podcast_index_credentials', return_value=('key', 'secret'))
    def test_empty_results(self, mock_creds, mock_get, client):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {'feeds': []}
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        response = client.get('/api/v1/podcast-search?q=nonexistent')
        data = json.loads(response.data)
        assert data['results'] == []

    @patch('api.podcast_search.requests.get')
    @patch('api.podcast_search._get_podcast_index_credentials', return_value=('key', 'secret'))
    def test_timeout_returns_502(self, mock_creds, mock_get, client):
        import requests as req
        mock_get.side_effect = req.exceptions.Timeout()
        response = client.get('/api/v1/podcast-search?q=test')
        assert response.status_code == 502
        data = json.loads(response.data)
        assert 'timed out' in data['error'].lower()

    @patch('api.podcast_search.requests.get')
    @patch('api.podcast_search._get_podcast_index_credentials', return_value=('key', 'secret'))
    def test_connection_error_returns_502(self, mock_creds, mock_get, client):
        import requests as req
        mock_get.side_effect = req.exceptions.ConnectionError()
        response = client.get('/api/v1/podcast-search?q=test')
        assert response.status_code == 502

    @patch('api.podcast_search.requests.get')
    @patch('api.podcast_search._get_podcast_index_credentials', return_value=('key', 'secret'))
    def test_non_json_response_returns_502(self, mock_creds, mock_get, client):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.side_effect = ValueError("No JSON")
        mock_get.return_value = mock_resp

        response = client.get('/api/v1/podcast-search?q=test')
        assert response.status_code == 502
        data = json.loads(response.data)
        assert 'invalid response' in data['error'].lower()

    @patch('api.podcast_search.requests.get')
    @patch('api.podcast_search._get_podcast_index_credentials', return_value=('key', 'secret'))
    def test_auth_headers_sent(self, mock_creds, mock_get, client):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {'feeds': []}
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        client.get('/api/v1/podcast-search?q=test')
        call_kwargs = mock_get.call_args
        headers = call_kwargs.kwargs.get('headers') or call_kwargs[1].get('headers')
        assert 'X-Auth-Key' in headers
        assert headers['X-Auth-Key'] == 'key'
        assert 'X-Auth-Date' in headers
        assert 'Authorization' in headers
        assert 'User-Agent' in headers

    @patch('api.podcast_search.requests.get')
    @patch('api.podcast_search._get_podcast_index_credentials', return_value=('key', 'secret'))
    def test_missing_fields_default_to_empty(self, mock_creds, mock_get, client):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {'feeds': [{'id': 1}]}
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        response = client.get('/api/v1/podcast-search?q=test')
        data = json.loads(response.data)
        result = data['results'][0]
        assert result['title'] == ''
        assert result['feedUrl'] == ''
        assert result['author'] == ''
        assert result['artworkUrl'] == ''
