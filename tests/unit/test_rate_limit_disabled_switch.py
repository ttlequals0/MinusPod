"""MINUSPOD_RATE_LIMIT_DISABLED turns the app-wide limiter off."""
import os
import sys

import pytest
from flask import Flask
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

import api as api_module  # noqa: E402
from utils.ratelimit_storage import STORAGE_URI  # noqa: E402


def _client(monkeypatch):
    fresh = Limiter(key_func=get_remote_address,
                    default_limits=["200 per minute", "1000 per hour"],
                    storage_uri=STORAGE_URI)
    monkeypatch.setattr(api_module, 'limiter', fresh)
    app = Flask(__name__)

    @app.route('/ping')
    def ping():
        return 'ok'

    api_module.init_limiter(app)
    return app.test_client()


def _codes(client, n=300):
    return {client.get('/ping').status_code for _ in range(n)}


@pytest.mark.parametrize('value', ['true', '1', 'YES'])
def test_disabled_never_returns_429(monkeypatch, value):
    monkeypatch.setenv('MINUSPOD_RATE_LIMIT_DISABLED', value)
    assert _codes(_client(monkeypatch)) == {200}


def test_default_still_limits(monkeypatch):
    monkeypatch.delenv('MINUSPOD_RATE_LIMIT_DISABLED', raising=False)
    assert 429 in _codes(_client(monkeypatch))
