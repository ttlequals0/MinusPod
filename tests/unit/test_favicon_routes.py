"""Unit tests for favicon and apple-touch-icon short-circuit routes.

These routes prevent /favicon.ico and /apple-touch-icon*.png from falling through
to the /<slug> feed route, which would trigger expensive DB lookups.
"""
import os
import sys
import tempfile
import shutil

import pytest

# Create temp data dir and set env before any imports that touch /app/data
_test_data_dir = tempfile.mkdtemp(prefix='favicon_test_')
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
from main_app.routes import STATIC_DIR


@pytest.fixture
def client():
    """Flask test client."""
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c


class TestFaviconRoute:
    """Tests for /favicon.ico route."""

    def test_favicon_returns_svg(self, client):
        """GET /favicon.ico should return the SVG favicon with correct Content-Type."""
        resp = client.get('/favicon.ico')
        assert resp.status_code == 200
        assert resp.content_type == 'image/svg+xml'

    def test_favicon_has_content(self, client):
        """GET /favicon.ico should return non-empty response body."""
        resp = client.get('/favicon.ico')
        assert len(resp.data) > 0

    def test_favicon_does_not_trigger_feed_lookup(self, client):
        """GET /favicon.ico should not fall through to the /<slug> feed route."""
        from unittest.mock import patch
        with patch('main_app.routes.get_feed_map') as mock_feed_map:
            resp = client.get('/favicon.ico')
            assert resp.status_code == 200
            mock_feed_map.assert_not_called()


class TestAppleTouchIconRoute:
    """Tests for /apple-touch-icon*.png routes."""

    def test_apple_touch_icon_returns_png(self, client):
        """GET /apple-touch-icon.png should return PNG."""
        resp = client.get('/apple-touch-icon.png')
        assert resp.status_code == 200
        assert 'image/png' in resp.content_type

    def test_apple_touch_icon_precomposed(self, client):
        """GET /apple-touch-icon-precomposed.png should return the same icon."""
        resp = client.get('/apple-touch-icon-precomposed.png')
        assert resp.status_code == 200
        assert 'image/png' in resp.content_type

    def test_apple_touch_icon_120x120(self, client):
        """GET /apple-touch-icon-120x120.png should return the same icon."""
        resp = client.get('/apple-touch-icon-120x120.png')
        assert resp.status_code == 200
        assert 'image/png' in resp.content_type

    def test_apple_touch_icon_120x120_precomposed(self, client):
        """GET /apple-touch-icon-120x120-precomposed.png should return the same icon."""
        resp = client.get('/apple-touch-icon-120x120-precomposed.png')
        assert resp.status_code == 200
        assert 'image/png' in resp.content_type

    def test_apple_touch_icon_does_not_trigger_feed_lookup(self, client):
        """Apple touch icon routes should not fall through to the /<slug> feed route."""
        from unittest.mock import patch
        with patch('main_app.routes.get_feed_map') as mock_feed_map:
            resp = client.get('/apple-touch-icon.png')
            assert resp.status_code == 200
            mock_feed_map.assert_not_called()

    def test_apple_touch_icon_has_content(self, client):
        """GET /apple-touch-icon.png should return non-empty response body."""
        resp = client.get('/apple-touch-icon.png')
        assert len(resp.data) > 0


def teardown_module():
    """Clean up temp directory."""
    shutil.rmtree(_test_data_dir, ignore_errors=True)
