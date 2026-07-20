"""Unit tests for podcast search endpoint."""
import json
import os
from unittest.mock import patch, MagicMock

import pytest

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('podcast_search_test_')
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
    """Tests for credential resolution when PodcastIndex is the provider."""

    def test_no_credentials_returns_503(self, client):
        # Explicit podcastindex choice without credentials: clear error, not
        # a silent fallback to iTunes the user did not pick.
        with patch('api.podcast_search.resolve_search_provider',
                   return_value='podcastindex'), \
             patch.dict(os.environ, {}, clear=False):
            os.environ.pop('PODCAST_INDEX_API_KEY', None)
            os.environ.pop('PODCAST_INDEX_API_SECRET', None)
            response = client.get('/api/v1/podcast-search?q=test')
        assert response.status_code == 503
        data = json.loads(response.data)
        assert 'credentials' in data['error'].lower()

    def test_key_without_secret_returns_503(self, client):
        with patch('api.podcast_search.resolve_search_provider',
                   return_value='podcastindex'), \
             patch.dict(os.environ, {'PODCAST_INDEX_API_KEY': 'key'}, clear=False):
            os.environ.pop('PODCAST_INDEX_API_SECRET', None)
            response = client.get('/api/v1/podcast-search?q=test')
        assert response.status_code == 503


class TestSearchProviderResolution:
    """resolve_search_provider: explicit choice wins; unset preserves the
    pre-option behavior of installs with PodcastIndex credentials."""

    def _clear_provider_setting(self):
        from api.podcast_search import get_database
        get_database().set_setting('podcast_search_provider', '')

    def test_unset_without_credentials_is_itunes(self, client):
        self._clear_provider_setting()
        with patch('api.podcast_search._get_podcast_index_credentials',
                   return_value=('', '')):
            from api.podcast_search import resolve_search_provider
            assert resolve_search_provider() == 'itunes'

    def test_unset_with_credentials_is_podcastindex(self, client):
        self._clear_provider_setting()
        with patch('api.podcast_search._get_podcast_index_credentials',
                   return_value=('key', 'secret')):
            from api.podcast_search import resolve_search_provider
            assert resolve_search_provider() == 'podcastindex'

    def test_explicit_itunes_wins_over_credentials(self, client):
        from api.podcast_search import get_database, resolve_search_provider
        get_database().set_setting('podcast_search_provider', 'itunes')
        try:
            with patch('api.podcast_search._get_podcast_index_credentials',
                       return_value=('key', 'secret')):
                assert resolve_search_provider() == 'itunes'
        finally:
            self._clear_provider_setting()

    def test_settings_put_validates_provider(self, client):
        r = client.put('/api/v1/settings/ad-detection',
                       data=json.dumps({'podcastSearchProvider': 'bogus'}),
                       content_type='application/json')
        assert r.status_code == 400

    def test_settings_put_and_get_round_trip(self, client):
        r = client.put('/api/v1/settings/ad-detection',
                       data=json.dumps({'podcastSearchProvider': 'itunes'}),
                       content_type='application/json')
        assert r.status_code == 200
        g = client.get('/api/v1/settings')
        assert g.get_json()['podcastSearchProvider']['value'] == 'itunes'
        self._clear_provider_setting()


class TestItunesSearch:
    """iTunes provider: keyless search mapped to the shared result shape."""

    def _mock_resp(self, results):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {'results': results}
        mock_resp.raise_for_status = MagicMock()
        return mock_resp

    @patch('api.podcast_search.safe_get')
    @patch('api.podcast_search.resolve_search_provider', return_value='itunes')
    def test_itunes_search_maps_fields(self, mock_provider, mock_get, client):
        mock_get.return_value = self._mock_resp([{
            'collectionId': 42,
            'collectionName': 'Test Show',
            'artistName': 'Host Name',
            'artworkUrl600': 'https://img/600.jpg',
            'feedUrl': 'https://example.com/rss',
            'collectionViewUrl': 'https://podcasts.apple.com/x',
            'primaryGenreName': 'Technology',
        }])
        response = client.get('/api/v1/podcast-search?q=test')
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data['provider'] == 'itunes'
        r = data['results'][0]
        assert r['id'] == 42
        assert r['title'] == 'Test Show'
        assert r['author'] == 'Host Name'
        assert r['artworkUrl'] == 'https://img/600.jpg'
        assert r['feedUrl'] == 'https://example.com/rss'
        url = mock_get.call_args[0][0]
        assert url.startswith('https://itunes.apple.com/search?')
        assert 'media=podcast' in url

    @patch('api.podcast_search.safe_get')
    @patch('api.podcast_search.resolve_search_provider', return_value='itunes')
    def test_itunes_drops_results_without_feed_url(self, mock_provider, mock_get, client):
        mock_get.return_value = self._mock_resp([
            {'collectionId': 1, 'collectionName': 'No Feed'},
            {'collectionId': 2, 'collectionName': 'Has Feed',
             'feedUrl': 'https://example.com/rss'},
        ])
        response = client.get('/api/v1/podcast-search?q=test')
        data = json.loads(response.data)
        assert [r['id'] for r in data['results']] == [2]

    @patch('api.podcast_search.safe_get')
    @patch('api.podcast_search.resolve_search_provider', return_value='itunes')
    def test_itunes_sends_no_auth_headers(self, mock_provider, mock_get, client):
        mock_get.return_value = self._mock_resp([])
        client.get('/api/v1/podcast-search?q=test')
        headers = mock_get.call_args[1]['headers']
        assert 'X-Auth-Key' not in headers
        assert 'Authorization' not in headers


class TestPodcastSearchAPICall:
    """Tests for PodcastIndex API interaction."""

    @patch('api.podcast_search.safe_get')
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

    @patch('api.podcast_search.safe_get')
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

    @patch('api.podcast_search.safe_get')
    @patch('api.podcast_search._get_podcast_index_credentials', return_value=('key', 'secret'))
    def test_empty_results(self, mock_creds, mock_get, client):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {'feeds': []}
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        response = client.get('/api/v1/podcast-search?q=nonexistent')
        data = json.loads(response.data)
        assert data['results'] == []

    @patch('api.podcast_search.safe_get')
    @patch('api.podcast_search._get_podcast_index_credentials', return_value=('key', 'secret'))
    def test_timeout_returns_502(self, mock_creds, mock_get, client):
        import requests as req
        mock_get.side_effect = req.exceptions.Timeout()
        response = client.get('/api/v1/podcast-search?q=test')
        assert response.status_code == 502
        data = json.loads(response.data)
        assert 'timed out' in data['error'].lower()

    @patch('api.podcast_search.safe_get')
    @patch('api.podcast_search._get_podcast_index_credentials', return_value=('key', 'secret'))
    def test_connection_error_returns_502(self, mock_creds, mock_get, client):
        import requests as req
        mock_get.side_effect = req.exceptions.ConnectionError()
        response = client.get('/api/v1/podcast-search?q=test')
        assert response.status_code == 502

    @patch('api.podcast_search.safe_get')
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

    @patch('api.podcast_search.safe_get')
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

    @patch('api.podcast_search.safe_get')
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
