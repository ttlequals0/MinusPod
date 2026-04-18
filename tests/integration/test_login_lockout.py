"""End-to-end tests for /auth/login lockout behavior."""
import json
import os
import sys
import tempfile

import pytest

_test_data_dir = tempfile.mkdtemp(prefix='lockout_test_')
os.environ.setdefault('SECRET_KEY', 'lockout-test-secret')
os.environ.setdefault('DATA_DIR', _test_data_dir)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

import database
import storage as storage_mod
database.Database._instance = None
database.Database.__init__.__defaults__ = (_test_data_dir,)
storage_mod.Storage.__init__.__defaults__ = (_test_data_dir,)

from main_app import app
from database.auth_lockout import LOCKOUT_THRESHOLD


@pytest.fixture
def client():
    db = database.Database()
    conn = db.get_connection()
    conn.execute("DELETE FROM auth_failures")
    conn.commit()
    from werkzeug.security import generate_password_hash
    db.set_setting('app_password', generate_password_hash('correct-password-123456', method='scrypt'))
    app.config['TESTING'] = True
    from api import limiter
    limiter.enabled = False
    limiter.reset()
    try:
        with app.test_client() as c:
            yield c
    finally:
        limiter.enabled = True


def _login(client, password, ip='8.8.4.4'):
    return client.post(
        '/api/v1/auth/login',
        data=json.dumps({'password': password}),
        content_type='application/json',
        environ_base={'REMOTE_ADDR': ip},
    )


def test_wrong_password_returns_401_before_lockout(client):
    for _ in range(LOCKOUT_THRESHOLD - 1):
        r = _login(client, 'wrong')
        assert r.status_code == 401


def test_lockout_triggers_after_threshold(client):
    for _ in range(LOCKOUT_THRESHOLD):
        _login(client, 'wrong')
    r = _login(client, 'correct-password-123456')
    assert r.status_code == 429
    assert r.headers.get('Retry-After')


def test_private_ip_is_not_locked_out(client):
    for _ in range(LOCKOUT_THRESHOLD + 2):
        r = _login(client, 'wrong', ip='192.168.1.10')
        # Private IP: every failed attempt keeps returning 401, never 429.
        assert r.status_code == 401


def test_successful_login_clears_counter(client):
    for _ in range(LOCKOUT_THRESHOLD - 1):
        _login(client, 'wrong')
    r = _login(client, 'correct-password-123456')
    assert r.status_code == 200
    for _ in range(LOCKOUT_THRESHOLD - 1):
        r = _login(client, 'wrong')
        assert r.status_code == 401
