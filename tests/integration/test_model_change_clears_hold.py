"""A stage-model change lifts an active rate-limit hold (issue #747): the new
model may have entirely different limits, so the old cooldown is meaningless."""
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='model-change-hold-test-'))


@pytest.fixture
def _db(app_client):
    from api import get_database
    db = get_database()
    yield db
    db.set_setting('rate_limit_hold_enabled', 'false')
    db.set_setting('rate_limit_hold_until', '')


def _csrf(app_client, db):
    from api.auth_state import current_generation
    with app_client.session_transaction() as sess:
        sess['authenticated'] = True
        # Match the DB generation so an auth test earlier in the suite that
        # set a password or revoked sessions cannot 401 this request.
        sess['auth_generation'] = current_generation(db)
    app_client.get('/api/v1/auth/status')
    cookie = app_client.get_cookie('minuspod_csrf')
    return {'X-CSRF-Token': cookie.value} if cookie else {}


def _hold(db):
    from rate_limit_hold import record_hold_until, any_hold_active
    from utils.time import utc_now
    from datetime import timedelta
    db.set_setting('rate_limit_hold_enabled', 'true')
    future = (utc_now() + timedelta(hours=6)).strftime('%Y-%m-%dT%H:%M:%SZ')
    record_hold_until(db, None, future)
    assert any_hold_active(db)


def test_changing_detection_model_clears_hold(app_client, _db):
    from rate_limit_hold import any_hold_active
    hdr = _csrf(app_client, _db)
    _db.set_setting('claude_model', 'old-model', is_default=False)
    _hold(_db)
    r = app_client.put('/api/v1/settings/ad-detection',
                       json={'claudeModel': 'new-model'}, headers=hdr)
    assert r.status_code == 200
    assert not any_hold_active(_db)


def test_settings_change_without_model_leaves_hold(app_client, _db):
    from rate_limit_hold import any_hold_active
    hdr = _csrf(app_client, _db)
    _db.set_setting('claude_model', 'same-model', is_default=False)
    _hold(_db)
    r = app_client.put('/api/v1/settings/ad-detection',
                       json={'claudeModel': 'same-model'}, headers=hdr)
    assert r.status_code == 200
    assert any_hold_active(_db)
