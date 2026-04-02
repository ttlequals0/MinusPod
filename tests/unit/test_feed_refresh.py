"""Tests for feed refresh logic -- N+1 query fix and bulk episode lookup."""
import pytest
from unittest.mock import MagicMock, patch

from database.episodes import EpisodeMixin


class MockDB(EpisodeMixin):
    """Minimal mock DB for testing episode status lookup."""

    def __init__(self, episodes):
        self._episodes = episodes
        self._podcast = {'id': 1, 'slug': 'test-pod'}

    def get_connection(self):
        """Return mock connection that returns our episodes."""
        mock_conn = MagicMock()
        rows = []
        for ep in self._episodes:
            row = MagicMock()
            row.__getitem__ = lambda self, key, ep=ep: ep[key]
            rows.append(row)
        mock_conn.execute.return_value.fetchall.return_value = rows
        return mock_conn

    def get_podcast_by_slug(self, slug):
        return self._podcast


class TestGetEpisodeStatusesForPodcast:
    """Test the bulk episode status lookup method."""

    def test_returns_empty_for_unknown_podcast(self):
        db = MockDB([])
        db.get_podcast_by_slug = MagicMock(return_value=None)
        id_map, title_map = db.get_episode_statuses_for_podcast('unknown')
        assert id_map == {}
        assert title_map == {}

    def test_returns_id_to_status_map(self):
        episodes = [
            {'episode_id': 'ep1', 'status': 'discovered', 'title': 'Ep 1', 'published_at': '2026-01-01T00:00:00Z'},
            {'episode_id': 'ep2', 'status': 'processed', 'title': 'Ep 2', 'published_at': '2026-01-02T00:00:00Z'},
            {'episode_id': 'ep3', 'status': 'queued', 'title': 'Ep 3', 'published_at': '2026-01-03T00:00:00Z'},
        ]
        db = MockDB(episodes)
        id_map, title_map = db.get_episode_statuses_for_podcast('test-pod')

        assert id_map == {
            'ep1': 'discovered',
            'ep2': 'processed',
            'ep3': 'queued',
        }

    def test_returns_title_date_to_id_map(self):
        episodes = [
            {'episode_id': 'ep1', 'status': 'discovered', 'title': 'Ep 1', 'published_at': '2026-01-01T00:00:00Z'},
            {'episode_id': 'ep2', 'status': 'processed', 'title': 'Ep 2', 'published_at': '2026-01-02T00:00:00Z'},
        ]
        db = MockDB(episodes)
        _, title_map = db.get_episode_statuses_for_podcast('test-pod')

        assert title_map == {
            ('Ep 1', '2026-01-01T00:00:00Z'): 'ep1',
            ('Ep 2', '2026-01-02T00:00:00Z'): 'ep2',
        }

    def test_skips_title_date_entry_when_missing(self):
        episodes = [
            {'episode_id': 'ep1', 'status': 'discovered', 'title': None, 'published_at': '2026-01-01T00:00:00Z'},
            {'episode_id': 'ep2', 'status': 'discovered', 'title': 'Ep 2', 'published_at': None},
        ]
        db = MockDB(episodes)
        id_map, title_map = db.get_episode_statuses_for_podcast('test-pod')

        assert len(id_map) == 2
        assert len(title_map) == 0

    def test_empty_episode_list(self):
        db = MockDB([])
        id_map, title_map = db.get_episode_statuses_for_podcast('test-pod')
        assert id_map == {}
        assert title_map == {}
