from tests.app_bootstrap import bootstrap
_test_data_dir = bootstrap('session_idle_test_')
import pytest
from flask import session
from werkzeug.security import generate_password_hash
from database import Database
from main_app import app
import api.auth_state as auth_state

T0 = 1_000_000.0


@pytest.fixture
def db():
    d = Database()
    d.set_setting('app_password', generate_password_hash('correct-password-123456', method='scrypt'))
    d.set_setting('session_lifetime_hours', '', is_default=True)
    d.set_setting('session_remember_days', '', is_default=True)
    yield d
    d.set_setting('app_password', '')


@pytest.fixture
def clock(monkeypatch):
    t = [T0]
    monkeypatch.setattr(auth_state, '_now', lambda: t[0])
    return t


@pytest.fixture
def make_session(db, clock):
    ctxs = []

    def build(remembered=False, **overrides):
        ctx = app.test_request_context('/api/v1/health')
        ctx.push()
        ctxs.append(ctx)
        values = {
            'authenticated': True,
            'auth_generation': auth_state.current_generation(db),
            'auth_remembered': remembered,
            'auth_last_used': clock[0],
        }
        values.update(overrides)
        for key, value in values.items():
            if value is not None:
                session[key] = value
        return session

    yield build
    for ctx in reversed(ctxs):
        ctx.pop()


def test_normal_session_past_hours_is_signed_out(db, clock, make_session):
    sess = make_session()
    clock[0] += 24 * 3600 + 1
    assert auth_state.session_is_authenticated(db) is False
    assert 'authenticated' not in sess


def test_normal_session_at_the_limit_is_still_signed_in(db, clock, make_session):
    make_session()
    clock[0] += 24 * 3600
    assert auth_state.session_is_authenticated(db) is True


def test_remembered_session_past_hours_still_authenticated(db, clock, make_session):
    make_session(remembered=True)
    clock[0] += 24 * 3600 + 1
    assert auth_state.session_is_authenticated(db) is True


def test_remembered_session_past_days_is_signed_out(db, clock, make_session):
    make_session(remembered=True)
    clock[0] += 30 * 86400 + 1
    assert auth_state.session_is_authenticated(db) is False


def test_use_inside_limit_extends_last_used(db, clock, make_session):
    sess = make_session()
    clock[0] += 120
    assert auth_state.session_is_authenticated(db) is True
    assert sess['auth_last_used'] == clock[0]


def test_continuous_use_outlives_the_limit(db, clock, make_session):
    make_session()
    for _ in range(30):
        clock[0] += 3600
        assert auth_state.session_is_authenticated(db) is True


def test_refresh_at_most_once_a_minute(db, clock, make_session):
    sess = make_session()
    clock[0] += 30
    assert auth_state.session_is_authenticated(db) is True
    assert sess['auth_last_used'] == T0


def test_lost_refresh_never_signs_out(db, clock, make_session):
    # A response carrying an older auth_last_used overwrites a newer cookie.
    sess = make_session()
    clock[0] += 24 * 3600 - 10
    assert auth_state.session_is_authenticated(db) is True
    sess['auth_last_used'] = T0
    clock[0] += 5
    assert auth_state.session_is_authenticated(db) is True


def test_pre_upgrade_session_is_stamped_on_first_use(db, clock, make_session):
    sess = make_session(auth_remembered=None, auth_last_used=None)
    assert auth_state.session_is_authenticated(db) is True
    assert sess['auth_last_used'] == T0
    clock[0] += 24 * 3600 + 1
    assert auth_state.session_is_authenticated(db) is False


def test_garbage_last_used_is_restamped_not_fatal(db, clock, make_session):
    sess = make_session(auth_last_used='yesterday')
    assert auth_state.session_is_authenticated(db) is True
    assert sess['auth_last_used'] == T0


def test_future_last_used_is_not_expired(db, clock, make_session):
    make_session(auth_last_used=T0 + 500)
    assert auth_state.session_is_authenticated(db) is True


def test_changed_setting_applies_without_restart(db, clock, make_session):
    make_session()
    db.set_setting('session_lifetime_hours', '1', is_default=False)
    clock[0] += 3600 + 1
    assert auth_state.session_is_authenticated(db) is False


def test_lengthened_setting_keeps_an_otherwise_expired_session(db, clock, make_session):
    make_session()
    db.set_setting('session_lifetime_hours', '48', is_default=False)
    clock[0] += 30 * 3600
    assert auth_state.session_is_authenticated(db) is True


def test_stale_generation_is_signed_out(db, clock, make_session):
    make_session(auth_generation=-1)
    assert auth_state.session_is_authenticated(db) is False


def test_no_password_signs_nothing_out(db, clock, make_session):
    db.set_setting('app_password', '')
    sess = make_session()
    clock[0] += 400 * 86400
    assert auth_state.session_is_authenticated(db) is True
    auth_state.enforce_idle_limit(db)
    assert sess.get('authenticated') is True


def test_anonymous_session_is_untouched(db, clock, make_session):
    sess = make_session(authenticated=None)
    auth_state.enforce_idle_limit(db)
    assert dict(sess) == {
        'auth_generation': auth_state.current_generation(db),
        'auth_remembered': False, 'auth_last_used': T0}


def test_authenticate_session_records_state(db, clock):
    with app.test_request_context('/api/v1/health'):
        auth_state.authenticate_session(db, remembered=True)
        assert session['auth_remembered'] is True
        assert session['auth_last_used'] == T0
        auth_state.authenticate_session(db)
        assert session['auth_remembered'] is False
