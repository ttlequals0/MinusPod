"""StatusService.remove_queued_episode supports cancelling queued episodes.

Regression: clicking Cancel on a queued episode returned 400 because the
endpoint only accepted status='processing'. The dequeue helper added here
gives the endpoint a way to drop a queued episode from the display queue.
"""
import os

import pytest


@pytest.fixture
def status_service(temp_dir, monkeypatch):
    """Fresh StatusService bound to a temp status file.

    Short-circuit the soft-timeout DB lookup so this unit test does not
    spin up the api package (which loads the full Flask app).
    """
    import status_service as status_service_mod
    monkeypatch.setattr(status_service_mod, '_get_soft_timeout', lambda: 3600)
    status_service_mod.StatusService._instance = None
    monkeypatch.setattr(
        status_service_mod, 'STATUS_FILE',
        os.path.join(temp_dir, 'processing_status.json'),
    )
    ss = status_service_mod.StatusService()
    yield ss
    status_service_mod.StatusService._instance = None


class TestRemoveQueuedEpisode:
    def test_removes_matching_episode(self, status_service):
        status_service.queue_episode('pod', 'ep1', 'Title', 'Pod')
        assert status_service.remove_queued_episode('pod', 'ep1') is True
        assert status_service.get_status().queued_episodes == []

    def test_returns_false_when_absent(self, status_service):
        assert status_service.remove_queued_episode('pod', 'missing') is False

    def test_leaves_other_episodes(self, status_service):
        status_service.queue_episode('pod', 'ep1', 'A', 'Pod')
        status_service.queue_episode('pod', 'ep2', 'B', 'Pod')
        assert status_service.remove_queued_episode('pod', 'ep1') is True
        remaining = status_service.get_status().queued_episodes
        assert [e['episode_id'] for e in remaining] == ['ep2']

    def test_distinguishes_by_slug(self, status_service):
        status_service.queue_episode('pod-a', 'shared-id', 'A', 'Pod A')
        status_service.queue_episode('pod-b', 'shared-id', 'B', 'Pod B')
        assert status_service.remove_queued_episode('pod-a', 'shared-id') is True
        remaining = status_service.get_status().queued_episodes
        assert [(e['slug'], e['episode_id']) for e in remaining] == [('pod-b', 'shared-id')]
