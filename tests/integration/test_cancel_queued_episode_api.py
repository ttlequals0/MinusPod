"""Cancel endpoint must dequeue queued episodes (not just processing ones).

Regression: POST /feeds/<slug>/episodes/<id>/cancel returned HTTP 400 for
queued episodes because it only accepted status='processing'. Requires the
full app environment, so it is skipped outside Docker.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

# Skip outside the Docker container with full dependencies (heavy main_app import).
pytest.importorskip("ctranslate2", reason="Integration tests require Docker environment")


@pytest.fixture
def status_service(temp_dir, monkeypatch):
    import status_service as status_service_mod
    status_service_mod.StatusService._instance = None
    monkeypatch.setattr(
        status_service_mod, 'STATUS_FILE',
        os.path.join(temp_dir, 'processing_status.json'),
    )
    ss = status_service_mod.StatusService()
    yield ss
    status_service_mod.StatusService._instance = None


def test_cancel_queued_episode_dequeues(
    temp_db, mock_podcast, status_service, app_client
):
    slug = mock_podcast['slug']
    episode_id = 'queued-ep-1'
    url = 'https://example.com/queued.mp3'

    # Queued episode: pending DB row + display queue + auto_process_queue row.
    temp_db.upsert_episode(slug, episode_id, original_url=url,
                           title='Queued Ep', status='pending')
    temp_db.queue_episode_for_processing(slug, episode_id, url, title='Queued Ep')
    status_service.queue_episode(slug, episode_id, 'Queued Ep', mock_podcast['title'])

    resp = app_client.post(f'/api/v1/feeds/{slug}/episodes/{episode_id}/cancel')

    assert resp.status_code == 200
    assert status_service.get_status().queued_episodes == []
    # auto_process_queue row closed so the background worker won't pick it up.
    assert temp_db.get_next_queued_episode() is None


def test_cancel_unqueued_pending_episode_400(
    temp_db, mock_episode, status_service, app_client
):
    # mock_episode is 'pending' but neither queued nor in auto_process_queue.
    slug = mock_episode['slug']
    episode_id = mock_episode['episode_id']

    resp = app_client.post(f'/api/v1/feeds/{slug}/episodes/{episode_id}/cancel')

    assert resp.status_code == 400
