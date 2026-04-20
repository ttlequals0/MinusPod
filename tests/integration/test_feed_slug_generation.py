"""Tests for POST /api/v1/feeds auto-slug generation (2.0.4 retry + 400 fallback)."""
import json
import os
import tempfile
from unittest.mock import patch

import pytest

# Mirror the module-level DATA_DIR setup used by other integration tests so
# importing main_app does not try to create /app/data when the test collects.
_test_data_dir = tempfile.mkdtemp(prefix='podcast_slug_test_')
os.environ.setdefault('DATA_DIR', _test_data_dir)


class _FakeParsedFeed:
    def __init__(self, title: str):
        self.feed = {'title': title}


def _post_feed(app_client, payload, auth_token: str = None):
    headers = {}
    if auth_token:
        headers['Authorization'] = f'Bearer {auth_token}'
    return app_client.post(
        '/api/v1/feeds',
        data=json.dumps(payload),
        content_type='application/json',
        headers=headers,
    )


class TestAutoSlugGeneration:
    """POST /api/v1/feeds without `slug` — title fetch with retry."""

    def test_slug_from_title_on_first_try(self, app_client):
        with patch('rss_parser.RSSParser') as MockParser, \
             patch('api.feeds.time.sleep') as mock_sleep:
            parser = MockParser.return_value
            parser.fetch_feed.return_value = '<rss/>'
            parser.parse_feed.return_value = _FakeParsedFeed('Maintenance Phase')

            resp = _post_feed(app_client, {
                'sourceUrl': 'https://example.com/feed.rss',
            })

        if resp.status_code in (401, 403):
            pytest.skip(f"Auth-gated deployment returned {resp.status_code}")

        assert parser.fetch_feed.call_count == 1
        mock_sleep.assert_not_called()
        assert resp.status_code in (201, 409)
        if resp.status_code == 201:
            body = json.loads(resp.data)
            assert body['slug'] == 'maintenance-phase'

    def test_retry_recovers_title_on_transient_failure(self, app_client):
        with patch('rss_parser.RSSParser') as MockParser, \
             patch('api.feeds.time.sleep') as mock_sleep:
            parser = MockParser.return_value
            parser.fetch_feed.side_effect = [None, '<rss/>']
            parser.parse_feed.return_value = _FakeParsedFeed('Maintenance Phase')

            resp = _post_feed(app_client, {
                'sourceUrl': 'https://example.com/feed.rss',
            })

        if resp.status_code in (401, 403):
            pytest.skip(f"Auth-gated deployment returned {resp.status_code}")

        assert parser.fetch_feed.call_count == 2
        mock_sleep.assert_called_once_with(0.5)
        assert resp.status_code in (201, 409)
        if resp.status_code == 201:
            body = json.loads(resp.data)
            assert body['slug'] == 'maintenance-phase'

    def test_both_fetches_fail_returns_400_no_url_fallback(self, app_client):
        with patch('rss_parser.RSSParser') as MockParser, \
             patch('api.feeds.time.sleep'):
            parser = MockParser.return_value
            parser.fetch_feed.return_value = None

            resp = _post_feed(app_client, {
                'sourceUrl': 'https://rss.buzzsprout.com/1411126.rss',
            })

        if resp.status_code in (401, 403):
            pytest.skip(f"Auth-gated deployment returned {resp.status_code}")

        assert parser.fetch_feed.call_count == 2
        assert resp.status_code == 400
        body = json.loads(resp.data)
        assert 'slug' in body.get('error', '').lower()
        # Critically: no numeric fallback slug is returned or committed.
        assert '1411126' not in json.dumps(body)

    def test_user_supplied_slug_bypasses_fetch(self, app_client):
        with patch('rss_parser.RSSParser') as MockParser, \
             patch('api.feeds.time.sleep') as mock_sleep:
            parser = MockParser.return_value

            resp = _post_feed(app_client, {
                'sourceUrl': 'https://rss.buzzsprout.com/1411126.rss',
                'slug': 'maintenancephase',
            })

        if resp.status_code in (401, 403):
            pytest.skip(f"Auth-gated deployment returned {resp.status_code}")

        parser.fetch_feed.assert_not_called()
        mock_sleep.assert_not_called()
        assert resp.status_code in (201, 409)
