"""Status validators cover every field in the returned representation."""
from unittest.mock import MagicMock, patch

from tests.app_bootstrap import bootstrap

bootstrap('status_etag_test_')

from main_app import app
from api.status import get_status, status_payload


def test_status_etag_changes_when_auxiliary_state_changes():
    first = {'revision': 7, 'hold': {'queuePaused': False}}
    second = {'revision': 7, 'hold': {'queuePaused': True}}

    with app.test_request_context('/api/v1/status'):
        with patch('api.status.status_payload', return_value=first):
            first_etag = get_status().headers['ETag']
        with patch('api.status.status_payload', return_value=second):
            second_etag = get_status().headers['ETag']

    assert first_etag != second_etag


def test_status_queue_length_includes_database_backlog_and_display_extras():
    db = MagicMock()
    db.count_pending_queued_episodes.return_value = 1000
    db.get_episode_job_states.return_value = {
        ('example-podcast', 'queued-in-db'): 'queued',
    }
    status_service = MagicMock()
    status_service.to_dict.return_value = {
        'queueLength': 2,
        'queuedEpisodes': [
            {'slug': 'example-podcast', 'episodeId': 'queued-in-db'},
            {'slug': 'example-podcast', 'episodeId': 'display-only'},
        ],
    }
    pool = MagicMock()
    pool.snapshot.return_value = {}

    with patch('api.status.get_database', return_value=db), \
         patch('api.status.get_status_service', return_value=status_service), \
         patch('api.status.hold_block', return_value={}), \
         patch('api.status.is_processing_paused', return_value=False), \
         patch('api.status.get_pool', return_value=pool):
        payload = status_payload()

    assert payload['queueLength'] == 1001
