"""StatusService.remove_queued_episode supports cancelling queued episodes.

The cancel endpoint used to accept only status='processing' and returned 400 for queued episodes.
"""
import pytest


@pytest.fixture
def status_service(temp_dir, monkeypatch):
    """Fresh StatusService bound to a temp status file.

    Short-circuits the soft-timeout DB lookup so the api package (and the full Flask app) is not loaded.
    """
    import status_service as status_service_mod
    monkeypatch.setattr(status_service_mod, '_get_soft_timeout', lambda: 3600)
    status_service_mod.StatusService._instance = None
    monkeypatch.setenv('DATA_DIR', temp_dir)
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


class TestRemoveFeedFromQueue:
    """remove_feed_from_queue drops every queued episode for one feed (#525)."""

    def test_removes_all_episodes_for_feed(self, status_service):
        status_service.queue_episode('pod', 'ep1', 'A', 'Pod')
        status_service.queue_episode('pod', 'ep2', 'B', 'Pod')
        assert status_service.remove_feed_from_queue('pod') == 2
        assert status_service.get_status().queued_episodes == []

    def test_leaves_other_feeds(self, status_service):
        status_service.queue_episode('pod-a', 'ep1', 'A', 'Pod A')
        status_service.queue_episode('pod-b', 'ep2', 'B', 'Pod B')
        assert status_service.remove_feed_from_queue('pod-a') == 1
        remaining = status_service.get_status().queued_episodes
        assert [(e['slug'], e['episode_id']) for e in remaining] == [('pod-b', 'ep2')]

    def test_returns_zero_when_feed_absent(self, status_service):
        status_service.queue_episode('pod-a', 'ep1', 'A', 'Pod A')
        assert status_service.remove_feed_from_queue('missing') == 0
        assert len(status_service.get_status().queued_episodes) == 1
