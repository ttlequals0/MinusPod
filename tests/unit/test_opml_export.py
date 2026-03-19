"""Unit tests for OPML export endpoint (mode=original and mode=modified)."""
import json
import os
import sys
import tempfile
import xml.etree.ElementTree as ET
from unittest.mock import patch

import pytest

# Create temp data dir and set env before any imports that touch /app/data
_test_data_dir = tempfile.mkdtemp(prefix='opml_export_test_')
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


def _parse_opml(response_data):
    """Parse OPML XML from response bytes."""
    return ET.fromstring(response_data)


def _mock_podcasts():
    """Return sample podcast data for tests."""
    return [
        {'slug': 'my-podcast', 'title': 'My Podcast', 'source_url': 'https://example.com/feed.xml'},
        {'slug': 'another-show', 'title': 'Another Show', 'source_url': 'https://other.com/rss'},
    ]


class TestOpmlExportModeValidation:
    """Tests for mode parameter validation."""

    @patch('api.feeds.get_database')
    def test_invalid_mode_returns_400(self, mock_db, client):
        response = client.get('/api/v1/feeds/export-opml?mode=invalid')
        assert response.status_code == 400
        data = json.loads(response.data)
        assert 'mode' in data['error']

    @patch('api.feeds.get_database')
    def test_default_mode_is_original(self, mock_db, client):
        mock_db.return_value.get_all_podcasts.return_value = []
        response = client.get('/api/v1/feeds/export-opml')
        assert response.status_code == 200
        assert 'minuspod-feeds.opml' in response.headers['Content-Disposition']


class TestOpmlExportOriginalMode:
    """Tests for mode=original (default behavior)."""

    @patch('api.feeds.get_database')
    def test_original_mode_uses_source_urls(self, mock_db, client):
        mock_db.return_value.get_all_podcasts.return_value = _mock_podcasts()
        response = client.get('/api/v1/feeds/export-opml?mode=original')
        assert response.status_code == 200

        root = _parse_opml(response.data)
        outlines = root.findall('.//outline')
        assert len(outlines) == 2
        assert outlines[0].get('xmlUrl') == 'https://example.com/feed.xml'
        assert outlines[1].get('xmlUrl') == 'https://other.com/rss'

    @patch('api.feeds.get_database')
    def test_original_mode_filename(self, mock_db, client):
        mock_db.return_value.get_all_podcasts.return_value = []
        response = client.get('/api/v1/feeds/export-opml?mode=original')
        assert 'minuspod-feeds.opml' in response.headers['Content-Disposition']

    @patch('api.feeds.get_database')
    def test_original_mode_title_fallback_to_slug(self, mock_db, client):
        mock_db.return_value.get_all_podcasts.return_value = [
            {'slug': 'untitled-pod', 'title': '', 'source_url': 'https://example.com/feed.xml'},
        ]
        response = client.get('/api/v1/feeds/export-opml?mode=original')
        root = _parse_opml(response.data)
        outline = root.find('.//outline')
        assert outline.get('text') == 'untitled-pod'


class TestOpmlExportModifiedMode:
    """Tests for mode=modified (MinusPod ad-free URLs)."""

    @patch('api.feeds.get_database')
    def test_modified_mode_uses_base_url_and_slug(self, mock_db, client):
        mock_db.return_value.get_all_podcasts.return_value = _mock_podcasts()
        with patch.dict(os.environ, {'BASE_URL': 'https://pod.example.com'}):
            response = client.get('/api/v1/feeds/export-opml?mode=modified')
        assert response.status_code == 200

        root = _parse_opml(response.data)
        outlines = root.findall('.//outline')
        assert outlines[0].get('xmlUrl') == 'https://pod.example.com/my-podcast'
        assert outlines[1].get('xmlUrl') == 'https://pod.example.com/another-show'

    @patch('api.feeds.get_database')
    def test_modified_mode_filename(self, mock_db, client):
        mock_db.return_value.get_all_podcasts.return_value = []
        response = client.get('/api/v1/feeds/export-opml?mode=modified')
        assert 'minuspod-feeds-modified.opml' in response.headers['Content-Disposition']

    @patch('api.feeds.get_database')
    def test_modified_mode_strips_trailing_slash(self, mock_db, client):
        mock_db.return_value.get_all_podcasts.return_value = [
            {'slug': 'test-pod', 'title': 'Test', 'source_url': 'https://example.com/feed'},
        ]
        with patch.dict(os.environ, {'BASE_URL': 'https://pod.example.com/'}):
            response = client.get('/api/v1/feeds/export-opml?mode=modified')
        root = _parse_opml(response.data)
        url = root.find('.//outline').get('xmlUrl')
        assert url == 'https://pod.example.com/test-pod'
        assert '//' not in url.split('://')[1]

    @patch('api.feeds.get_database')
    def test_modified_mode_default_base_url(self, mock_db, client):
        mock_db.return_value.get_all_podcasts.return_value = [
            {'slug': 'pod', 'title': 'Pod', 'source_url': ''},
        ]
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('BASE_URL', None)
            response = client.get('/api/v1/feeds/export-opml?mode=modified')
        root = _parse_opml(response.data)
        assert root.find('.//outline').get('xmlUrl') == 'http://localhost:8000/pod'


class TestOpmlExportStructure:
    """Tests for OPML XML structure."""

    @patch('api.feeds.get_database')
    def test_opml_has_correct_structure(self, mock_db, client):
        mock_db.return_value.get_all_podcasts.return_value = _mock_podcasts()
        response = client.get('/api/v1/feeds/export-opml')

        root = _parse_opml(response.data)
        assert root.tag == 'opml'
        assert root.get('version') == '2.0'
        assert root.find('head/title').text == 'MinusPod Feeds'
        assert root.find('body') is not None

    @patch('api.feeds.get_database')
    def test_outlines_have_required_attributes(self, mock_db, client):
        mock_db.return_value.get_all_podcasts.return_value = _mock_podcasts()
        response = client.get('/api/v1/feeds/export-opml')

        root = _parse_opml(response.data)
        for outline in root.findall('.//outline'):
            assert outline.get('type') == 'rss'
            assert outline.get('text') is not None
            assert outline.get('title') is not None
            assert outline.get('xmlUrl') is not None

    @patch('api.feeds.get_database')
    def test_content_type_is_xml(self, mock_db, client):
        mock_db.return_value.get_all_podcasts.return_value = []
        response = client.get('/api/v1/feeds/export-opml')
        assert 'application/xml' in response.content_type

    @patch('api.feeds.get_database')
    def test_empty_feeds_exports_valid_opml(self, mock_db, client):
        mock_db.return_value.get_all_podcasts.return_value = []
        response = client.get('/api/v1/feeds/export-opml')
        assert response.status_code == 200
        root = _parse_opml(response.data)
        assert root.findall('.//outline') == []
