"""Integration tests for the queue hold block on GET /status.

The hold block reports what the maintenance tick last observed, so the UI can
tell a genuinely idle queue apart from a paused or waiting one.
"""
import os
import sys
import tempfile
from datetime import timedelta

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='status-hold-test-'))

HOLD_KEYS = {'queuePaused', 'holdUntil', 'holdSince', 'rateLimitHeld',
             'offlineHeld', 'offlineServices'}


@pytest.fixture
def clean_hold(app_client):
    """A queue with no hold recorded and no deferred episodes."""
    from api import get_database
    from api.status import _hold_cache, _hold_cache_lock
    from config import DEFER_SERVICE_LLM, DEFER_SERVICE_WHISPER
    from offline_queue import probe_state_keys
    from rate_limit_hold import HOLD_UNTIL_KEY

    db = get_database()
    keys = [HOLD_UNTIL_KEY]
    for service in (DEFER_SERVICE_LLM, DEFER_SERVICE_WHISPER):
        keys.extend(probe_state_keys(service))

    def reset():
        for key in keys:
            db.clear_setting(key)
        with _hold_cache_lock:
            _hold_cache.clear()

    reset()
    yield db
    reset()


@pytest.fixture
def deferred_on(clean_hold):
    """Park one episode on a named service, cleaned up afterwards."""
    from contextlib import contextmanager

    @contextmanager
    def park(service, slug='hold-block-feed'):
        clean_hold.create_podcast(
            slug, 'https://example.com/feed.xml', title='Hold Block Test')
        try:
            clean_hold.upsert_episode(
                slug, 'ep-1', title='Episode 1', status='deferred',
                deferred_service=service)
            yield clean_hold
        finally:
            clean_hold.delete_podcast(slug)

    return park


def _hold(client):
    """Read the hold block, dropping the response cache so a just-written
    setting is visible instead of a stale frame."""
    from api.status import _hold_cache, _hold_cache_lock
    with _hold_cache_lock:
        _hold_cache.clear()
    response = client.get('/api/v1/status')
    assert response.status_code == 200
    return response.get_json()['hold']


def test_status_reports_an_empty_hold_when_nothing_is_held(clean_hold, app_client):
    hold = _hold(app_client)
    assert set(hold) == HOLD_KEYS
    assert hold['queuePaused'] is False
    assert hold['holdUntil'] is None
    assert hold['holdSince'] is None
    assert hold['rateLimitHeld'] == 0
    assert hold['offlineHeld'] == 0
    assert hold['offlineServices'] == []


def test_a_future_reset_time_reports_the_queue_as_paused(clean_hold, app_client):
    from rate_limit_hold import record_hold_until
    from utils.time import utc_now

    reset_at = (utc_now() + timedelta(minutes=30)).isoformat()
    record_hold_until(clean_hold, reset_at)

    hold = _hold(app_client)
    assert hold['queuePaused'] is True
    assert hold['holdUntil'] == reset_at


def test_a_past_reset_time_is_not_a_pause(clean_hold, app_client):
    from rate_limit_hold import HOLD_UNTIL_KEY
    from utils.time import utc_now

    clean_hold.set_setting(
        HOLD_UNTIL_KEY, (utc_now() - timedelta(minutes=5)).isoformat())
    assert _hold(app_client)['queuePaused'] is False


def test_offline_services_report_the_last_probe_verdict(deferred_on, app_client):
    from config import DEFER_SERVICE_WHISPER
    from offline_queue import record_probe_state

    with deferred_on(DEFER_SERVICE_WHISPER) as db:
        record_probe_state(db, DEFER_SERVICE_WHISPER, False)
        hold = _hold(app_client)
        assert hold['offlineHeld'] == 1
        assert hold['offlineServices'] == [{
            'service': DEFER_SERVICE_WHISPER, 'held': 1, 'reachable': False,
            'checkedAt': hold['offlineServices'][0]['checkedAt'],
        }]
        assert hold['offlineServices'][0]['checkedAt']
        # An offline wait parks specific episodes; it does not pause the queue.
        assert hold['queuePaused'] is False


def test_an_unprobed_service_reports_reachable_as_unknown(deferred_on, app_client):
    """None, not False: before the first tick we have not checked, which is
    not the same as having found the service down."""
    from config import DEFER_SERVICE_LLM

    with deferred_on(DEFER_SERVICE_LLM):
        entry = _hold(app_client)['offlineServices'][0]
        assert entry['reachable'] is None
        assert entry['checkedAt'] is None
