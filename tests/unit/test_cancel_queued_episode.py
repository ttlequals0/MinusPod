"""StatusService.remove_queued_episode -- supports cancelling queued episodes.

Regression: clicking Cancel on a queued episode in the Settings processing
queue did nothing because there was no way to drop it from the display queue.
The endpoint-level behaviour is covered in
tests/integration/test_cancel_queued_episode_api.py (Docker only).
"""
import os

import pytest


@pytest.fixture
def status_service(temp_dir, monkeypatch):
    """Fresh StatusService bound to a temp status file."""
    import status_service as status_service_mod
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
        status_service.remove_queued_episode('pod', 'ep1')
        remaining = status_service.get_status().queued_episodes
        assert [e['episode_id'] for e in remaining] == ['ep2']
