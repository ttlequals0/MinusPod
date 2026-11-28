"""API tokens: management routes, bearer authentication, the session-only deny-list, lockout."""
import logging
import sqlite3

import pytest
from werkzeug.security import generate_password_hash

from tests.app_bootstrap import bootstrap
from tests.integration.openapi_conformance import assert_response_conforms

_test_data_dir = bootstrap('api_tokens_test_', secret_key='api-tokens-test-secret')

import database
from api import limiter
from api.auth_state import SESSION_ONLY_ENDPOINTS
from database.auth_lockout import LOCKOUT_THRESHOLD
from main_app import app
from utils.http import redact_log_text

PW = 'correct-password-123456'
PUBLIC_IP = '8.8.4.4'


def _fake_token():
    return 'mp_' + 'x' * 43


@pytest.fixture
def db():
    d = database.Database()
    conn = d.get_connection()
    conn.execute("DELETE FROM api_tokens")
    conn.execute("DELETE FROM auth_failures")
    conn.commit()
    d.set_setting('app_password', generate_password_hash(PW, method='scrypt'))
    app.config['TESTING'] = True
    limiter.enabled = False
    yield d
    limiter.enabled = True
    d.set_setting('app_password', '')
    conn.execute("DELETE FROM api_tokens")
    conn.commit()


@pytest.fixture
def session_client(db):
    c = app.test_client()
    assert c.post('/api/v1/auth/login', json={'password': PW}).status_code == 200
    return c


@pytest.fixture
def token_client(db):
    return app.test_client()


def _csrf(client):
    return {'X-CSRF-Token': client.get_cookie('minuspod_csrf').value}


def _create(client, name='home-assistant'):
    return client.post('/api/v1/auth/tokens', json={'name': name}, headers=_csrf(client))


def _bearer(token):
    return {'Authorization': f'Bearer {token}'}


@pytest.fixture
def token(session_client):
    return _create(session_client).get_json()['token']


# ---- management ----

def test_create_returns_plaintext_once_and_stores_only_the_hash(session_client, db):
    resp = _create(session_client)
    assert_response_conforms('post', '/auth/tokens', resp, 201)
    body = resp.get_json()
    plaintext = body['token']
    assert plaintext.startswith('mp_') and len(plaintext) == 46
    assert body['prefix'] == plaintext[3:11]
    assert resp.headers['Cache-Control'] == 'no-store'

    listing = session_client.get('/api/v1/auth/tokens').get_json()['tokens']
    assert [t['name'] for t in listing] == ['home-assistant']
    assert set(listing[0]) == {'id', 'name', 'prefix', 'createdAt', 'lastUsedAt'}
    assert plaintext not in str(listing)

    row = db.get_connection().execute("SELECT * FROM api_tokens").fetchone()
    assert plaintext not in tuple(row)
    assert row['token_hash'] == database.api_tokens.hash_token(plaintext)


def test_revoke_then_unknown_is_404(session_client):
    token_id = _create(session_client).get_json()['id']
    url = f'/api/v1/auth/tokens/{token_id}'
    assert session_client.delete(url, headers=_csrf(session_client)).status_code == 200
    assert session_client.delete(url, headers=_csrf(session_client)).status_code == 404
    assert session_client.get('/api/v1/auth/tokens').get_json()['tokens'] == []


def test_management_needs_csrf_for_a_session(session_client):
    assert session_client.post('/api/v1/auth/tokens', json={'name': 'x'}).status_code == 403


@pytest.mark.parametrize('payload', [{}, {'name': ''}, {'name': '   '}, {'name': 'a' * 65}, {'name': 5}, None])
def test_name_validation(session_client, payload):
    assert _create_raw(session_client, payload).status_code == 400


def _create_raw(client, payload):
    return client.post('/api/v1/auth/tokens', json=payload, headers=_csrf(client))


def test_name_is_trimmed_and_unique_among_active_tokens(session_client):
    first = _create(session_client, '  ci  ').get_json()
    assert first['name'] == 'ci'
    assert _create(session_client, 'CI').status_code == 409
    session_client.delete(f"/api/v1/auth/tokens/{first['id']}", headers=_csrf(session_client))
    assert _create(session_client, 'ci').status_code == 201


def test_active_token_cap(session_client):
    for i in range(25):
        assert _create(session_client, f'token-{i}').status_code == 201
    assert _create(session_client, 'one-too-many').status_code == 409


def test_create_without_a_password_is_409(db):
    db.set_setting('app_password', '')
    resp = app.test_client().post('/api/v1/auth/tokens', json={'name': 'x'})
    assert resp.status_code == 409
    assert 'password' in resp.get_json()['error']


NEW_PW = 'another-password-123456'


def _put_password(client, **body):
    return client.put('/api/v1/auth/password', json={'currentPassword': PW, 'newPassword': NEW_PW, **body})


def test_changing_the_password_revokes_tokens_by_default(session_client, db):
    _create(session_client, 'one')
    _create(session_client, 'two')
    resp = _put_password(session_client)
    assert resp.status_code == 200
    assert resp.get_json()['apiTokensRevoked'] == 2
    assert db.list_api_tokens() == []


def test_changing_the_password_can_keep_tokens(session_client, db):
    _create(session_client)
    resp = _put_password(session_client, revokeApiTokens=False)
    assert resp.status_code == 200
    assert resp.get_json()['apiTokensRevoked'] == 0
    assert len(db.list_api_tokens()) == 1


@pytest.mark.parametrize('value', ['false', 0, 1, None, []])
def test_non_boolean_revoke_flag_is_400(session_client, db, value):
    _create(session_client)
    assert _put_password(session_client, revokeApiTokens=value).status_code == 400
    assert len(db.list_api_tokens()) == 1


def test_first_setup_ignores_the_revoke_flag(db):
    db.set_setting('app_password', '')
    resp = app.test_client().put('/api/v1/auth/password', json={'newPassword': NEW_PW, 'revokeApiTokens': False})
    assert resp.status_code == 200
    assert resp.get_json()['apiTokensRevoked'] == 0


def test_removing_the_password_revokes_tokens_even_when_asked_to_keep_them(session_client, db):
    _create(session_client)
    removed = session_client.put('/api/v1/auth/password', json={
        'currentPassword': PW, 'newPassword': '', 'revokeApiTokens': False})
    assert removed.status_code == 200
    assert removed.get_json()['apiTokensRevoked'] == 1
    assert db.list_api_tokens() == []


def test_revoke_all_clears_every_token_and_is_session_only(session_client, token_client, db):
    first = _create(session_client, 'one').get_json()['token']
    _create(session_client, 'two')
    assert token_client.delete('/api/v1/auth/tokens', headers=_bearer(first)).status_code == 403
    assert len(db.list_api_tokens()) == 2
    resp = session_client.delete('/api/v1/auth/tokens', headers=_csrf(session_client))
    assert resp.status_code == 200 and resp.get_json() == {'revoked': 2}
    assert db.list_api_tokens() == []
    assert token_client.get('/api/v1/feeds', headers=_bearer(first)).status_code == 401


# ---- bearer authentication ----

def test_bearer_reads_without_a_cookie(token_client, token):
    resp = token_client.get('/api/v1/feeds', headers=_bearer(token))
    assert resp.status_code == 200
    assert token_client.get_cookie('minuspod_csrf') is None


def test_bearer_mutates_without_csrf(token_client, token):
    assert token_client.delete('/api/v1/system/queue').status_code == 401
    assert token_client.delete('/api/v1/system/queue', headers=_bearer(token)).status_code == 200


def test_session_cookie_still_needs_csrf(session_client):
    assert session_client.delete('/api/v1/system/queue').status_code == 403


def test_revoked_bearer_does_not_fall_back_to_the_cookie(session_client, token):
    session_client.delete('/api/v1/auth/tokens/%d' % session_client.get(
        '/api/v1/auth/tokens').get_json()['tokens'][0]['id'], headers=_csrf(session_client))
    resp = session_client.get('/api/v1/feeds', headers=_bearer(token))
    assert resp.status_code == 401


def test_cookie_plus_valid_bearer_gets_no_csrf_cookie_or_session_refresh(session_client, token):
    resp = session_client.delete('/api/v1/system/queue', headers=_bearer(token))
    assert resp.status_code == 200
    assert not any(h.startswith('session=') for h in resp.headers.getlist('Set-Cookie'))


@pytest.mark.parametrize('bearer', ['mp_short', 'mp_' + '!' * 43, _fake_token()])
def test_bad_tokens_get_401_and_no_cookie_fallback(session_client, bearer):
    resp = session_client.get('/api/v1/feeds', headers=_bearer(bearer))
    assert resp.status_code == 401
    assert bearer not in resp.get_data(as_text=True)


def test_revoked_token_is_401(token_client, session_client, token):
    token_id = session_client.get('/api/v1/auth/tokens').get_json()['tokens'][0]['id']
    session_client.delete(f'/api/v1/auth/tokens/{token_id}', headers=_csrf(session_client))
    assert token_client.get('/api/v1/feeds', headers=_bearer(token)).status_code == 401


def test_non_mp_bearer_and_other_schemes_are_ignored(session_client):
    for header in ('Bearer some.proxy.jwt', 'Basic dXNlcjpwdw=='):
        assert session_client.get('/api/v1/feeds', headers={'Authorization': header}).status_code == 200


def test_bearer_is_ignored_when_no_password_is_set(db):
    db.set_setting('app_password', '')
    resp = app.test_client().get('/api/v1/feeds', headers=_bearer(_fake_token()))
    assert resp.status_code == 200


def test_bearer_lockout_is_separate_from_the_password_login(token_client, token):
    environ = {'REMOTE_ADDR': PUBLIC_IP}
    for _ in range(LOCKOUT_THRESHOLD):
        resp = token_client.get('/api/v1/feeds', headers=_bearer(_fake_token()), environ_base=environ)
        assert resp.status_code == 401
    locked = token_client.get('/api/v1/feeds', headers=_bearer(token), environ_base=environ)
    assert locked.status_code == 429
    assert locked.headers['Retry-After'].isdigit()
    login = token_client.post('/api/v1/auth/login', json={'password': PW}, environ_base=environ)
    assert login.status_code == 200


def test_failed_logins_never_block_a_valid_token(token_client, token):
    environ = {'REMOTE_ADDR': PUBLIC_IP}
    for _ in range(LOCKOUT_THRESHOLD):
        bad = token_client.post('/api/v1/auth/login', json={'password': 'wrong'}, environ_base=environ)
        assert bad.status_code == 401
    assert token_client.post('/api/v1/auth/login', json={'password': PW},
                             environ_base=environ).status_code == 429
    assert token_client.get('/api/v1/feeds', headers=_bearer(token), environ_base=environ).status_code == 200


def test_private_ip_failures_never_lock_out(token_client):
    for _ in range(LOCKOUT_THRESHOLD + 2):
        resp = token_client.get('/api/v1/feeds', headers=_bearer(_fake_token()),
                                environ_base={'REMOTE_ADDR': '192.168.1.10'})
        assert resp.status_code == 401


def test_last_used_is_stamped_at_most_every_five_minutes(token_client, token, db, monkeypatch):
    def stamp():
        return db.get_connection().execute("SELECT last_used_at FROM api_tokens").fetchone()[0]
    assert stamp() is None
    token_client.get('/api/v1/feeds', headers=_bearer(token))
    first = stamp()
    assert first
    token_client.get('/api/v1/feeds', headers=_bearer(token))
    assert stamp() == first
    db.get_connection().execute("UPDATE api_tokens SET last_used_at = '2000-01-01T00:00:00Z'")
    db.get_connection().commit()
    token_client.get('/api/v1/feeds', headers=_bearer(token))
    assert stamp() != '2000-01-01T00:00:00Z'


def test_a_request_inside_the_window_issues_no_update(token_client, token, db):
    token_client.get('/api/v1/feeds', headers=_bearer(token))
    statements = []
    conn = db.get_connection()
    conn.set_trace_callback(statements.append)
    try:
        assert token_client.get('/api/v1/feeds', headers=_bearer(token)).status_code == 200
    finally:
        conn.set_trace_callback(None)
    assert not [s for s in statements if 'UPDATE api_tokens' in s]


def test_a_failing_stamp_does_not_fail_the_request(token_client, token, db, monkeypatch):
    def boom(*_a, **_k):
        raise sqlite3.OperationalError('database is locked')
    monkeypatch.setattr(database.Database, 'transaction', boom)
    assert token_client.get('/api/v1/feeds', headers=_bearer(token)).status_code == 200


def test_tokens_never_reach_the_logs(token_client, session_client, token, caplog):
    bad = _fake_token()
    with caplog.at_level(logging.DEBUG):
        token_client.get('/api/v1/feeds', headers=_bearer(token))
        token_client.get('/api/v1/feeds', headers=_bearer(bad))
        _create(session_client, 'logged')
    assert token not in caplog.text
    assert bad not in caplog.text


def test_log_redaction_covers_token_and_bearer_shapes():
    token = _fake_token()
    for text in (f'sent {token} here', f'Authorization: Bearer {token}', f'headers={{"Authorization": "Bearer {token}"}}'):
        assert token not in redact_log_text(text)
    assert redact_log_text('no secrets here') == 'no secrets here'
    jwt = 'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijklmnop'
    assert jwt not in redact_log_text(f'Authorization: Bearer {jwt}')


@pytest.mark.parametrize('key', ['abc123XYZ9', 'k3y-with.19chars_x', 'AbCdEfGh12345678', 'token-s3cr3tvalue99', 'auth.s3cr3tvalue99'])
def test_log_redaction_covers_short_bearer_keys(key):
    assert key not in redact_log_text(f'Authorization: Bearer {key}')


@pytest.mark.parametrize('prose', [
    'Missing Bearer authorization header', 'Invalid bearer credentials', 'expected a Bearer token',
    'Bearer token required', 'Bearer token.', 'Bearer token, please', 'Bearer authorization: missing',
])
def test_log_redaction_leaves_bearer_prose_alone(prose):
    assert redact_log_text(prose) == prose


# ---- session-only deny-list ----

EXPECTED_SESSION_ONLY = {
    'api.list_api_tokens', 'api.create_api_token', 'api.revoke_api_token', 'api.revoke_all_api_tokens',
    'api.auth_set_password', 'api.auth_logout', 'api.rotate_master_passphrase',
    'api.get_sb_user_id',
    'api.preview_runtime_config_import', 'api.apply_runtime_config_import',
    'api.update_db_backup_settings',
}


def test_the_deny_list_is_exactly_the_reviewed_set():
    assert SESSION_ONLY_ENDPOINTS == EXPECTED_SESSION_ONLY


def test_token_can_run_a_backup_but_not_change_backup_settings(token_client, token, monkeypatch):
    monkeypatch.setattr('api.system.backup_now', lambda _db: {'path': '/x/y.db'})
    resp = token_client.put('/api/v1/settings/db-backup', json={'enabled': False}, headers=_bearer(token))
    assert resp.status_code == 403
    assert token_client.post('/api/v1/system/db-backup/run', headers=_bearer(token)).status_code == 200


def _deny_listed_requests():
    for endpoint in sorted(SESSION_ONLY_ENDPOINTS):
        rule = next(r for r in app.url_map.iter_rules() if r.endpoint == endpoint)
        method = sorted(rule.methods - {'HEAD', 'OPTIONS'})[0]
        args = {name: 1 for name in rule.arguments}
        yield endpoint, method, rule.build(args)[1]


def test_every_deny_listed_route_returns_403_for_a_token(token_client, token):
    seen = 0
    for endpoint, method, url in _deny_listed_requests():
        resp = token_client.open(url, method=method, headers=_bearer(token))
        assert resp.status_code == 403, endpoint
        assert 'session' in resp.get_json()['error']
        seen += 1
    assert seen == len(SESSION_ONLY_ENDPOINTS)


def test_deny_listed_routes_stay_403_with_cookie_and_bearer_together(session_client, token):
    for endpoint, method, url in _deny_listed_requests():
        headers = {**_bearer(token), **_csrf(session_client)}
        resp = session_client.open(url, method=method, headers=headers)
        assert resp.status_code == 403, endpoint


def test_a_token_can_export_configuration(token_client, token):
    assert token_client.get('/api/v1/system/config-export').status_code == 401
    assert token_client.get('/api/v1/system/config-export', headers=_bearer(token)).status_code == 200
    assert token_client.get('/api/v1/system/config-backup', headers=_bearer(token)).status_code == 200


def test_public_routes_need_no_auth(token_client, db):
    assert token_client.get('/api/v1/health').status_code in (200, 503)
    assert token_client.get('/api/v1/health/live').status_code == 200
    assert token_client.get('/api/v1/auth/status').get_json()['authenticated'] is False
    db.create_podcast('token-public-feed', 'https://example.com/feed.xml', title='Show')
    try:
        assert token_client.get('/token-public-feed').status_code not in (401, 403)
        assert token_client.get('/api/v1/feeds/token-public-feed/artwork').status_code != 401
    finally:
        db.delete_podcast('token-public-feed')


def test_existing_databases_gain_the_table_on_upgrade(db):
    conn = db.get_connection()
    conn.execute("DROP TABLE api_tokens")
    db._create_new_tables_only(conn)
    assert conn.execute("SELECT COUNT(*) FROM api_tokens").fetchone()[0] == 0


def test_status_stream_works_with_a_token_and_ends_on_revocation(token_client, token, db, monkeypatch):
    import api.status as status_mod
    monkeypatch.setattr(status_mod, 'status_payload', lambda: {'revision': 1})
    monkeypatch.setattr(status_mod.time, 'sleep', lambda _s: None)
    chunks = iter(token_client.get('/api/v1/status/stream', headers=_bearer(token), buffered=False).response)
    assert next(chunks).startswith(b'data:')
    db.revoke_all_api_tokens()
    assert next(chunks).startswith(b'event: auth-failed')


def test_status_stream_rejects_a_bad_token(token_client):
    resp = token_client.get('/api/v1/status/stream', headers=_bearer(_fake_token()), buffered=False)
    assert next(iter(resp.response)).startswith(b'event: auth-failed')
