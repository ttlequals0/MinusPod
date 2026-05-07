"""Integration tests for /original-segments and /final-segments endpoints."""
import pytest
import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

pytest.importorskip("ctranslate2", reason="Integration tests require Docker environment")


SEGMENTS = [
    {'start': 0.0, 'end': 5.5, 'text': 'Hello world.'},
    {'start': 5.5, 'end': 12.3, 'text': 'This is a test segment.'},
]


@pytest.fixture
def seeded_episode(app_client):
    """Create a podcast and episode via the app's database singleton."""
    from api import get_database

    db = get_database()
    slug = 'segments-test-feed'
    episode_id = 'segtest001'

    db.create_podcast(slug, 'https://example.com/feed.xml', 'Segments Test Feed')
    db.upsert_episode(slug, episode_id,
                      original_url='https://example.com/ep.mp3',
                      title='Test Episode',
                      status='pending')

    yield {'slug': slug, 'episode_id': episode_id, 'db': db}

    try:
        db.delete_podcast(slug)
    except Exception:
        pass


class TestOriginalSegmentsEndpoint:
    def test_returns_404_when_missing(self, app_client, seeded_episode):
        slug = seeded_episode['slug']
        ep_id = seeded_episode['episode_id']

        response = app_client.get(f'/api/v1/feeds/{slug}/episodes/{ep_id}/original-segments')

        assert response.status_code == 404

    def test_returns_200_with_payload_after_save(self, app_client, seeded_episode):
        slug = seeded_episode['slug']
        ep_id = seeded_episode['episode_id']
        db = seeded_episode['db']

        db.save_original_segments(slug, ep_id, SEGMENTS)

        response = app_client.get(f'/api/v1/feeds/{slug}/episodes/{ep_id}/original-segments')

        assert response.status_code == 200
        data = json.loads(response.data)
        assert data['episodeId'] == ep_id
        assert data['segments'] == SEGMENTS

    def test_returns_404_for_unknown_feed(self, app_client):
        response = app_client.get('/api/v1/feeds/no-such-feed/episodes/abc123/original-segments')

        assert response.status_code == 404


class TestFinalSegmentsEndpoint:
    def test_returns_404_when_missing(self, app_client, seeded_episode):
        slug = seeded_episode['slug']
        ep_id = seeded_episode['episode_id']

        response = app_client.get(f'/api/v1/feeds/{slug}/episodes/{ep_id}/final-segments')

        assert response.status_code == 404

    def test_returns_200_with_payload_after_save(self, app_client, seeded_episode):
        slug = seeded_episode['slug']
        ep_id = seeded_episode['episode_id']
        db = seeded_episode['db']

        db.save_final_segments(slug, ep_id, SEGMENTS)

        response = app_client.get(f'/api/v1/feeds/{slug}/episodes/{ep_id}/final-segments')

        assert response.status_code == 200
        data = json.loads(response.data)
        assert data['episodeId'] == ep_id
        assert data['segments'] == SEGMENTS

    def test_returns_404_for_unknown_feed(self, app_client):
        response = app_client.get('/api/v1/feeds/no-such-feed/episodes/abc123/final-segments')

        assert response.status_code == 404
