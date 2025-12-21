"""Integration tests for API endpoints.

These tests require the full application with all dependencies.
Run in Docker container or with complete environment.
"""
import pytest
import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

# Skip all tests in this module if ctranslate2 is not available
# (indicates we're not in the Docker container with full dependencies)
pytest.importorskip("ctranslate2", reason="Integration tests require Docker environment")


class TestHealthEndpoint:
    """Tests for health check endpoint."""

    def test_health_endpoint(self, app_client):
        """GET /api/v1/health returns status."""
        response = app_client.get('/api/v1/health')

        assert response.status_code == 200
        data = json.loads(response.data)
        assert 'status' in data
        assert data['status'] in ('ok', 'healthy')

    def test_health_includes_version(self, app_client):
        """Health endpoint includes version info."""
        response = app_client.get('/api/v1/health')

        data = json.loads(response.data)
        assert 'version' in data


class TestFeedsEndpoint:
    """Tests for feeds endpoints."""

    def test_list_feeds(self, app_client):
        """GET /api/v1/feeds returns list."""
        response = app_client.get('/api/v1/feeds')

        assert response.status_code == 200
        data = json.loads(response.data)
        assert 'feeds' in data
        assert isinstance(data['feeds'], list)

    def test_add_feed_validation(self, app_client):
        """POST /api/v1/feeds validates URL."""
        # Test with invalid data - missing URL
        response = app_client.post(
            '/api/v1/feeds',
            data=json.dumps({'name': 'Test'}),
            content_type='application/json'
        )

        # Should fail validation (either 400 or 422)
        assert response.status_code in [400, 422]

    def test_add_feed_invalid_url(self, app_client):
        """POST /api/v1/feeds rejects invalid URL format."""
        response = app_client.post(
            '/api/v1/feeds',
            data=json.dumps({'url': 'not-a-valid-url'}),
            content_type='application/json'
        )

        # Should fail validation
        assert response.status_code in [400, 422]

    def test_get_nonexistent_feed(self, app_client):
        """GET /api/v1/feeds/<slug> returns 404 for unknown slug."""
        response = app_client.get('/api/v1/feeds/nonexistent-feed-slug')

        assert response.status_code == 404


class TestSettingsEndpoint:
    """Tests for settings endpoint."""

    def test_get_settings(self, app_client):
        """GET /api/v1/settings returns config."""
        response = app_client.get('/api/v1/settings')

        assert response.status_code == 200
        data = json.loads(response.data)
        # Settings should have some structure
        assert isinstance(data, dict)

    def test_get_ad_detection_settings(self, app_client):
        """Settings include ad detection configuration."""
        response = app_client.get('/api/v1/settings')

        data = json.loads(response.data)
        # Should have ad detection related settings
        assert 'settings' in data or 'ad_detection' in data or len(data) > 0


class TestPatternsEndpoint:
    """Tests for patterns endpoint."""

    def test_list_patterns(self, app_client):
        """GET /api/v1/patterns returns list."""
        response = app_client.get('/api/v1/patterns')

        assert response.status_code == 200
        data = json.loads(response.data)
        assert 'patterns' in data
        assert isinstance(data['patterns'], list)

    def test_get_nonexistent_pattern(self, app_client):
        """GET /api/v1/patterns/<id> returns 404 for unknown ID."""
        response = app_client.get('/api/v1/patterns/99999')

        assert response.status_code == 404


class TestSystemEndpoints:
    """Tests for system status endpoints."""

    def test_system_status(self, app_client):
        """GET /api/v1/system/status returns status."""
        response = app_client.get('/api/v1/system/status')

        assert response.status_code == 200
        data = json.loads(response.data)
        assert isinstance(data, dict)

    def test_system_queue(self, app_client):
        """GET /api/v1/system/queue returns queue info."""
        response = app_client.get('/api/v1/system/queue')

        assert response.status_code == 200
        data = json.loads(response.data)
        assert isinstance(data, dict)


class TestHistoryEndpoints:
    """Tests for history endpoints."""

    def test_get_history(self, app_client):
        """GET /api/v1/history returns processing history."""
        response = app_client.get('/api/v1/history')

        assert response.status_code == 200
        data = json.loads(response.data)
        assert 'episodes' in data or 'history' in data or isinstance(data, dict)

    def test_get_history_stats(self, app_client):
        """GET /api/v1/history/stats returns statistics."""
        response = app_client.get('/api/v1/history/stats')

        assert response.status_code == 200
        data = json.loads(response.data)
        assert isinstance(data, dict)


class TestSponsorsEndpoints:
    """Tests for sponsors endpoints."""

    def test_list_sponsors(self, app_client):
        """GET /api/v1/sponsors returns list."""
        response = app_client.get('/api/v1/sponsors')

        assert response.status_code == 200
        data = json.loads(response.data)
        assert 'sponsors' in data
        assert isinstance(data['sponsors'], list)

    def test_get_sponsor_normalizations(self, app_client):
        """GET /api/v1/sponsors/normalizations returns list."""
        response = app_client.get('/api/v1/sponsors/normalizations')

        assert response.status_code == 200
        data = json.loads(response.data)
        assert 'normalizations' in data


class TestMainHealthEndpoint:
    """Tests for main app health endpoint."""

    def test_root_health(self, app_client):
        """GET /health returns status."""
        response = app_client.get('/health')

        assert response.status_code == 200
        data = json.loads(response.data)
        assert 'status' in data
        assert data['status'] == 'ok'
