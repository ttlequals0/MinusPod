"""Exception text stored on the episode, the history row and the webhook has no URL query or credential."""
from unittest.mock import patch

import pytest
import requests

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('failure_error_redaction_test_')
import differential_fetcher
import webhook_service
from api import error_response
from config import MAX_EPISODE_RETRIES
from main_app import app, db
from main_app.pipeline.failure import _handle_processing_failure
from rate_limit_hold import hold_message
from utils.errors import ServiceUnavailableError

SLUG = 'failure-redaction-feed'
KEY = 'ab' * 32

ERRORS = [
    requests.HTTPError('403 Client Error: Forbidden for url: https://cdn.example.com/x.mp3?token=SECRET'),
    requests.ConnectTimeout("HTTPSConnectionPool(host='cdn.example.com', port=443): "
                            "Max retries exceeded with url: /x.mp3?token=SECRET"),
    RuntimeError(f'download failed: https://u:SECRET@cdn.example.com/{KEY}/x.mp3'),
]


@pytest.fixture
def episode():
    db.create_podcast(SLUG, 'https://example.com/feed.xml', title='Failure Redaction Test')
    db.upsert_episode(SLUG, 'ep-1', title='Episode 1', status='processing',
                      original_url='https://example.com/ep1.mp3', retry_count=MAX_EPISODE_RETRIES)
    yield 'ep-1'
    db.delete_podcast(SLUG)


class _InlineThread:
    def __init__(self, target, args=(), kwargs=None, daemon=None):
        self._call = lambda: target(*args, **(kwargs or {}))

    def start(self):
        self._call()


def _fail(episode_id, error):
    """Run the failure handler; returns the contexts that reached webhook dispatch."""
    sent = []
    with patch('main_app.processing.status_service'), \
            patch.object(webhook_service, 'load_webhooks', lambda: [
                {'enabled': True, 'events': sorted(webhook_service.VALID_EVENTS), 'url': 'https://example.com/h'}]), \
            patch.object(webhook_service, '_prepare_and_dispatch', lambda wh, ctx: sent.append(ctx)), \
            patch.object(webhook_service, '_timestamp_fields', lambda: {}), \
            patch.object(webhook_service.email_service, 'send_event_email', lambda event, ctx: sent.append(ctx)), \
            patch.object(webhook_service.threading, 'Thread', _InlineThread):
        _handle_processing_failure(SLUG, episode_id, 'Episode 1', 'Failure Redaction Test',
                                   db.get_episode(SLUG, episode_id), error, start_time=0.0)
    return sent


def _assert_clean(text):
    assert text and 'SECRET' not in text and KEY not in text, text


@pytest.mark.parametrize('error', ERRORS, ids=['http-error', 'connect-timeout', 'userinfo-and-key'])
def test_permanent_failure_stores_and_sends_redacted_text(episode, error):
    sent = _fail(episode, error)
    _assert_clean(db.get_episode(SLUG, episode)['error_message'])
    rows, _ = db.get_processing_history(podcast_slug=SLUG)
    _assert_clean(rows[0]['error_message'])
    _assert_clean(str(sent[0]['episode']['error_message']))
    _assert_clean(str(sent[-1]['episode']['error_message']))  # the email context


def test_deferred_failure_stores_and_sends_redacted_text(episode):
    db.set_setting('offline_queue_enabled', 'true')
    try:
        sent = _fail(episode, ServiceUnavailableError(
            'llm', 'llm endpoint down: https://llm.example.com/v1?api_key=SECRET'))
    finally:
        db.set_setting('offline_queue_enabled', 'false')
    _assert_clean(db.get_episode(SLUG, episode)['error_message'])
    _assert_clean(str(sent[0]['error_message']))


def test_api_error_bodies_carry_no_query_or_credential():
    with app.app_context():
        body = error_response('fetch failed: https://u:SECRET@h/x?k=SECRET', 400,
                              details={'reason': '/f?token=SECRET'}).get_json()
        nested = error_response({'message': 'Sync failed', 'reason': 'https://h/m?k=SECRET'}, 502).get_json()
    assert 'SECRET' not in str(body) and 'SECRET' not in str(nested)
    assert body['error'] == 'fetch failed: https://<redacted>@h/x?<redacted>'
    assert nested['error'] == {'message': 'Sync failed', 'reason': 'https://h/m?<redacted>'}


@pytest.mark.parametrize('hold_until', [None, '2026-01-01T00:00:00Z'])
def test_hold_message_redacts_the_error(hold_until):
    _assert_clean(hold_message(hold_until, RuntimeError('429 at https://h/v1?key=SECRET')))


def test_differential_refetch_error_is_redacted(monkeypatch, tmp_path):
    def boom(*a, **k):
        raise requests.ConnectionError('Max retries exceeded with url: /x.mp3?token=SECRET')
    monkeypatch.setattr(differential_fetcher, 'safe_get', boom)
    run_file = tmp_path / 'run.mp3'
    run_file.write_bytes(b'x')
    result = differential_fetcher.fetch_and_diff(
        'https://cdn.example.com/x.mp3?token=SECRET', str(run_file), str(tmp_path), primary_user_agent='ua')
    assert result['status'] == 'error'
    _assert_clean(result['error'])
