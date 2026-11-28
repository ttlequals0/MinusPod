"""What an API token can reach: backup encryption, secret disclosure, and endpoint classification."""
import json
import re

import pytest
from werkzeug.security import generate_password_hash

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('api_token_scope_', secret_key='api-token-scope-secret',
                           passphrase='api-token-scope-passphrase')

import database
from api import limiter
from api.auth_state import SESSION_ONLY_ENDPOINTS
from main_app import app
from tests.integration.token_endpoint_classes import PUBLIC_ENDPOINTS, TOKEN_ALLOWED

PW = 'correct-password-123456'
PROVIDER_KEY = 'sk-ant-' + 'z' * 24
SMTP_PASSWORD = 'smtp-' + 'q' * 16
WEBHOOK_SECRET = 'hook-' + 'w' * 16


@pytest.fixture
def db():
    d = database.Database()
    conn = d.get_connection()
    conn.execute("DELETE FROM api_tokens")
    conn.commit()
    d.set_setting('app_password', generate_password_hash(PW, method='scrypt'))
    d.set_secret('anthropic_api_key', PROVIDER_KEY)
    d.set_secret('email_smtp_password', SMTP_PASSWORD)
    d.set_setting('webhooks', json.dumps([{
        'id': 'h1', 'url': 'https://example.com/hook', 'events': ['Episode Processed'],
        'enabled': True, 'secret': WEBHOOK_SECRET}]))
    app.config['TESTING'] = True
    limiter.enabled = False
    yield d
    limiter.enabled = True
    d.set_setting('app_password', '')
    d.set_setting('webhooks', '[]')
    conn.execute("DELETE FROM api_tokens")
    conn.commit()


@pytest.fixture
def token(db):
    client = app.test_client()
    assert client.post('/api/v1/auth/login', json={'password': PW}).status_code == 200
    csrf = client.get_cookie('minuspod_csrf').value
    resp = client.post('/api/v1/auth/tokens', json={'name': 'scope'}, headers={'X-CSRF-Token': csrf})
    return resp.get_json()['token']


def _get(token, path):
    return app.test_client().get(f'/api/v1{path}', headers={'Authorization': f'Bearer {token}'})


# ---- backups ----

def test_a_token_cannot_download_an_unencrypted_database(token):
    resp = _get(token, '/system/backup?encrypted=false')
    assert resp.status_code == 403
    assert resp.get_json()['error'] == 'API tokens can only download encrypted backups'


def test_a_token_gets_an_encrypted_backup_by_default_and_explicitly(token):
    for query in ('', '?encrypted=true'):
        resp = _get(token, f'/system/backup{query}')
        assert resp.status_code == 200, query
        assert resp.data.startswith(b'MPBK'), query
        assert b'SQLite format 3' not in resp.data[:64]


def test_a_token_gets_the_session_error_when_encryption_is_unavailable(token, monkeypatch):
    monkeypatch.delenv('MINUSPOD_MASTER_PASSPHRASE')
    resp = _get(token, '/system/backup')
    assert resp.status_code == 409
    assert resp.get_json()['error'] == 'backup_encryption_unavailable'


def test_a_session_can_still_download_a_plaintext_backup(db):
    client = app.test_client()
    client.post('/api/v1/auth/login', json={'password': PW})
    resp = client.get('/api/v1/system/backup?encrypted=false')
    assert resp.status_code == 200
    assert resp.data.startswith(b'SQLite format 3')


# ---- secret disclosure ----

def _forbidden(db):
    hashes = [r[0] for r in db.get_connection().execute("SELECT token_hash FROM api_tokens")]
    return {
        'flask secret key': db.get_setting('flask_secret_key') or app.secret_key,
        'password hash': db.get_setting('app_password'),
        **{f'token hash {i}': h for i, h in enumerate(hashes)},
    }


MASKED_GETS = [
    '/settings', '/settings/providers', '/settings/webhooks', '/settings/notifications/email',
    '/settings/db-backup', '/settings/youtube', '/feeds', '/feeds/subscriber-keys',
    '/system/status', '/system/diagnostic-export', '/system/config-export', '/auth/status',
]


@pytest.mark.parametrize('path', MASKED_GETS)
def test_masked_reads_never_disclose_operator_credentials_or_provider_keys(db, token, path):
    resp = _get(token, path)
    assert resp.status_code == 200, path
    body = resp.get_data(as_text=True)
    for label, value in _forbidden(db).items():
        assert value and value not in body, f'{path} leaks {label}'
    for secret in (PROVIDER_KEY, SMTP_PASSWORD, WEBHOOK_SECRET):
        assert secret not in body, f'{path} leaks a stored secret'


def test_config_backup_holds_provider_keys_but_no_operator_credentials(db, token):
    resp = _get(token, '/system/config-backup')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert PROVIDER_KEY in body
    for label, value in _forbidden(db).items():
        assert value and value not in body, label
    assert not re.search(r'flask_secret_key|app_password|auth_session_generation|api_tokens', body)


# ---- classification ----

def _api_endpoints():
    return {r.endpoint for r in app.url_map.iter_rules() if r.endpoint.startswith('api.')}


def test_every_api_endpoint_is_classified_exactly_once():
    classes = [SESSION_ONLY_ENDPOINTS, PUBLIC_ENDPOINTS, TOKEN_ALLOWED]
    for i, a in enumerate(classes):
        for b in classes[i + 1:]:
            assert not (a & b), a & b
    registered = _api_endpoints()
    unclassified = registered - set().union(*classes)
    assert not unclassified, (
        f'Classify {sorted(unclassified)}: add to SESSION_ONLY_ENDPOINTS (api/auth_state.py) or to '
        'TOKEN_ALLOWED (tests/integration/token_endpoint_classes.py) after checking it discloses no '
        'operator credential')
    stale = set().union(*classes) - registered
    assert not stale, f'Classified but not registered: {sorted(stale)}'
