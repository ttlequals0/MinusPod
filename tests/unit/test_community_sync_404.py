"""Test that the /community-patterns/sync endpoint returns a soft 200
when upstream has not yet published the manifest (404), rather than 502.

We avoid spinning up the full main_app (which probes /app/data, queues,
sentry, etc.) by invoking the route handler directly inside a Flask
test_request_context.
"""
import json
import os
import sys
from unittest.mock import patch

import pytest
import requests
from flask import Flask

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))


@pytest.fixture
def app(monkeypatch, tmp_path):
    monkeypatch.setenv('MINUSPOD_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('DATABASE_PATH', ':memory:')
    monkeypatch.setenv('AUTH_DISABLED', 'true')
    app = Flask(__name__)
    app.config['TESTING'] = True
    return app


def _fake_http_error(status: int) -> requests.HTTPError:
    resp = requests.Response()
    resp.status_code = status
    return requests.HTTPError(f'{status} error', response=resp)


def _call_sync(app, sync_side_effect):
    """Patch sync_now AND get_database (route calls both before our branch
    fires). Returns the Flask Response."""
    from api.settings import trigger_community_pattern_sync
    with app.test_request_context('/api/v1/community-patterns/sync', method='POST'), \
         patch('community_sync.sync_now', side_effect=sync_side_effect), \
         patch('api.settings.get_database', return_value=object()):
        return trigger_community_pattern_sync()


def _status_and_body(resp):
    """Route returns either a Response or a (Response, status) tuple."""
    if isinstance(resp, tuple):
        body_obj, status = resp[0], resp[1]
        return status, body_obj.get_json()
    return resp.status_code, resp.get_json()


def test_sync_404_returns_no_manifest_yet_not_502(app):
    resp = _call_sync(app, _fake_http_error(404))
    status, body = _status_and_body(resp)
    assert status == 200, body
    assert body['status'] == 'no_manifest_yet'


def test_sync_other_http_error_still_502(app):
    resp = _call_sync(app, _fake_http_error(500))
    status, _ = _status_and_body(resp)
    assert status == 502


def test_sync_generic_error_still_502(app):
    resp = _call_sync(app, RuntimeError('boom'))
    status, _ = _status_and_body(resp)
    assert status == 502
