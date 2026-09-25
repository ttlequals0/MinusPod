"""Legacy reprocess URL uses the mode-aware queue path."""
from unittest.mock import patch

import pytest

from tests.app_bootstrap import authenticate_test_client, bootstrap

bootstrap('legacy_reprocess_test_')


@pytest.fixture
def episode(app_client):
    from api import get_database, get_status_service, limiter

    limiter.reset()
    db = get_database()
    slug = 'legacy-reprocess-test'
    episode_id = 'aa11bb22cc33'
    db.create_podcast(slug, 'https://example.com/feed.xml', 'Example Podcast')
    db.upsert_episode(
        slug, episode_id, status='processed', title='Example Episode',
        original_url='https://example.com/episode.mp3',
    )
    db.save_episode_details(slug, episode_id, transcript_text='Saved transcript')
    db.save_original_transcript(slug, episode_id, 'Original transcript')
    segments = [{'start': 0.0, 'end': 2.0, 'text': 'Original transcript'}]
    db.save_original_segments(slug, episode_id, segments)
    headers = authenticate_test_client(app_client)
    yield db, slug, episode_id, segments, headers
    get_status_service().remove_feed_from_queue(slug)
    db.delete_podcast(slug)


def test_legacy_llm_mode_keeps_saved_transcript_while_queued(app_client, episode):
    db, slug, episode_id, segments, headers = episode
    with patch('main_app.processing.start_background_processing', return_value=(False, 'busy')):
        response = app_client.post(
            f'/api/v1/feeds/{slug}/episodes/{episode_id}/reprocess',
            json={'mode': 'llm'}, headers=headers,
        )

    assert response.status_code == 202
    assert response.get_json()['mode'] == 'llm'
    assert response.get_json()['status'] == 'queued'
    assert db.get_episode(slug, episode_id)['reprocess_mode'] == 'llm'
    assert db.has_transcript(slug, episode_id)
    assert db.get_original_transcript(slug, episode_id) == 'Original transcript'
    assert db.get_original_segments(slug, episode_id) == segments


def test_legacy_invalid_mode_leaves_saved_data_untouched(app_client, episode):
    db, slug, episode_id, segments, headers = episode
    response = app_client.post(
        f'/api/v1/feeds/{slug}/episodes/{episode_id}/reprocess',
        json={'mode': 'invalid'}, headers=headers,
    )

    assert response.status_code == 400
    assert db.get_episode(slug, episode_id)['status'] == 'processed'
    assert db.has_transcript(slug, episode_id)
    assert db.get_original_segments(slug, episode_id) == segments


@pytest.mark.parametrize('body', ['not JSON', '[]', 'null'])
def test_legacy_invalid_body_leaves_saved_data_untouched(app_client, episode, body):
    db, slug, episode_id, segments, headers = episode
    response = app_client.post(
        f'/api/v1/feeds/{slug}/episodes/{episode_id}/reprocess',
        data=body, content_type='application/json', headers=headers,
    )

    assert response.status_code == 400
    assert db.get_episode(slug, episode_id)['status'] == 'processed'
    assert db.has_transcript(slug, episode_id)
    assert db.get_original_segments(slug, episode_id) == segments


def test_legacy_default_reprocess_defers_detail_clear(app_client, episode):
    db, slug, episode_id, segments, headers = episode
    with patch('main_app.processing.start_background_processing', return_value=(False, 'busy')):
        response = app_client.post(
            f'/api/v1/feeds/{slug}/episodes/{episode_id}/reprocess',
            headers=headers,
        )

    assert response.status_code == 202
    assert response.get_json()['message'] == 'Episode queued for reprocess'
    assert response.get_json()['episodeId'] == episode_id
    assert db.get_episode(slug, episode_id)['reprocess_mode'] == 'reprocess'
    assert db.has_transcript(slug, episode_id)
    assert db.get_original_segments(slug, episode_id) == segments
