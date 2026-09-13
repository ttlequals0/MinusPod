"""Status validators cover every field in the returned representation."""
from unittest.mock import patch

from tests.app_bootstrap import bootstrap

bootstrap('status_etag_test_')

from main_app import app
from api.status import get_status


def test_status_etag_changes_when_auxiliary_state_changes():
    first = {'revision': 7, 'hold': {'queuePaused': False}}
    second = {'revision': 7, 'hold': {'queuePaused': True}}

    with app.test_request_context('/api/v1/status'):
        with patch('api.status.status_payload', return_value=first):
            first_etag = get_status().headers['ETag']
        with patch('api.status.status_payload', return_value=second):
            second_etag = get_status().headers['ETag']

    assert first_etag != second_etag
