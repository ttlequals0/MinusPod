"""Integration tests for scheduled DB backup endpoints (Task B465-2).

Hits the real Flask app and the real filesystem (tmp dirs), no mocks.

- GET /settings/db-backup default shape.
- PUT full + partial roundtrip.
- PUT 400s with exact messages (bad cron, keepCount bounds/type,
  relative dest, dest == data dir).
- POST /system/db-backup/run -> 200 + file exists, works with enabled=false.
- POST failure -> 500 flat message + GET surfaces lastError.
- PUT is all-or-nothing: a bad dest rolls back earlier valid fields.
- New routes absent from the auth-exempt allowlist.
"""
import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='db-backup-test-'))


DB_BACKUP_KEYS = (
    'db_backup_enabled', 'db_backup_cron', 'db_backup_dest',
    'db_backup_keep_count', 'db_backup_last_run', 'db_backup_last_error',
    'db_backup_last_summary',
)


@pytest.fixture
def db(app_client):
    """Fresh, no-password DB so requests skip auth/CSRF (community-sync path)."""
    from api import get_database
    d = get_database()
    d.set_setting('app_password', '')
    for key in DB_BACKUP_KEYS:
        d.set_setting(key, '')
    yield d
    for key in DB_BACKUP_KEYS:
        d.set_setting(key, '')


# -- GET defaults --

def test_get_defaults(app_client, db):
    r = app_client.get('/api/v1/settings/db-backup')
    assert r.status_code == 200
    body = r.get_json()
    assert body['enabled'] is False
    assert body['cron'] == '30 3 * * *'
    assert body['dest'] == ''
    assert body['effectiveDest'] == str((db.data_dir / 'backups').resolve())
    assert body['destWritable'] is True
    assert body['keepCount'] == 1
    assert body['lastRun'] is None
    assert body['lastError'] is None
    assert body['lastSummary'] is None


# -- PUT full + partial roundtrip --

def test_put_full_roundtrip(app_client, db):
    dest = tempfile.mkdtemp(prefix='db-backup-dest-')
    r = app_client.put('/api/v1/settings/db-backup', json={
        'enabled': True,
        'cron': '0 4 * * *',
        'dest': dest,
        'keepCount': 7,
    })
    assert r.status_code == 200
    body = r.get_json()
    assert body['enabled'] is True
    assert body['cron'] == '0 4 * * *'
    assert body['dest'] == dest
    assert body['effectiveDest'] == str(Path(dest).resolve())
    assert body['keepCount'] == 7


def test_put_partial_only_enabled(app_client, db):
    app_client.put('/api/v1/settings/db-backup', json={'cron': '0 5 * * *', 'keepCount': 3})
    r = app_client.put('/api/v1/settings/db-backup', json={'enabled': True})
    assert r.status_code == 200
    body = r.get_json()
    assert body['enabled'] is True
    assert body['cron'] == '0 5 * * *'
    assert body['keepCount'] == 3


def test_put_dest_empty_resets_to_default(app_client, db):
    dest = tempfile.mkdtemp(prefix='db-backup-dest-')
    app_client.put('/api/v1/settings/db-backup', json={'dest': dest})
    r = app_client.put('/api/v1/settings/db-backup', json={'dest': ''})
    assert r.status_code == 200
    body = r.get_json()
    assert body['dest'] == ''
    assert body['effectiveDest'] == str((db.data_dir / 'backups').resolve())


# -- PUT 400s with exact messages --

def test_put_bad_cron(app_client, db):
    r = app_client.put('/api/v1/settings/db-backup', json={'cron': 'not a cron'})
    assert r.status_code == 400
    assert r.get_json()['error'] == 'invalid cron expression: not a cron'


def test_put_keep_count_zero(app_client, db):
    r = app_client.put('/api/v1/settings/db-backup', json={'keepCount': 0})
    assert r.status_code == 400
    assert r.get_json()['error'] == 'keepCount must be an integer between 1 and 365'


def test_put_keep_count_366(app_client, db):
    r = app_client.put('/api/v1/settings/db-backup', json={'keepCount': 366})
    assert r.status_code == 400
    assert r.get_json()['error'] == 'keepCount must be an integer between 1 and 365'


def test_put_keep_count_string(app_client, db):
    r = app_client.put('/api/v1/settings/db-backup', json={'keepCount': '7'})
    assert r.status_code == 400
    assert r.get_json()['error'] == 'keepCount must be an integer between 1 and 365'


def test_put_keep_count_bool(app_client, db):
    r = app_client.put('/api/v1/settings/db-backup', json={'keepCount': True})
    assert r.status_code == 400
    assert r.get_json()['error'] == 'keepCount must be an integer between 1 and 365'


def test_put_dest_relative(app_client, db):
    r = app_client.put('/api/v1/settings/db-backup', json={'dest': 'relative/path'})
    assert r.status_code == 400
    assert r.get_json()['error'] == 'destination must be an absolute path'


def test_put_dest_is_data_dir(app_client, db):
    r = app_client.put('/api/v1/settings/db-backup', json={'dest': str(db.data_dir)})
    assert r.status_code == 400
    assert r.get_json()['error'] == 'destination must not be the data directory itself'


def test_put_all_or_nothing_on_bad_dest(app_client, db):
    # A later validation failure (bad dest) must roll back earlier valid fields:
    # nothing is persisted and GET reflects no change.
    r = app_client.put('/api/v1/settings/db-backup', json={
        'enabled': True,
        'cron': '0 4 * * *',
        'keepCount': 9,
        'dest': 'relative/path',
    })
    assert r.status_code == 400
    assert r.get_json()['error'] == 'destination must be an absolute path'
    # None of the earlier fields were committed.
    assert db.get_setting('db_backup_enabled') in (None, '', 'false')
    got = app_client.get('/api/v1/settings/db-backup').get_json()
    assert got['enabled'] is False
    assert got['cron'] == '30 3 * * *'
    assert got['keepCount'] == 1


# -- POST run --

def test_post_run_creates_file_when_disabled(app_client, db):
    dest = tempfile.mkdtemp(prefix='db-backup-run-')
    app_client.put('/api/v1/settings/db-backup', json={'dest': dest, 'enabled': False})
    r = app_client.post('/api/v1/system/db-backup/run')
    assert r.status_code == 200
    summary = r.get_json()
    assert summary['mode'] == 'overwrite'
    assert os.path.exists(summary['path'])
    assert summary['sizeBytes'] > 0
    assert os.path.exists(os.path.join(dest, 'minuspod-backup.db'))


def test_post_run_failure_returns_500_and_sets_last_error(app_client, db):
    # Point dest at a path whose parent is a regular file so the backup dir
    # cannot be created; the mkdir raises, backup_now stamps last_error and
    # re-raises, the handler returns 500 with a flat Error-schema message (no
    # nested reason, no raw str(e)). The operator detail surfaces via GET.
    blocker = tempfile.NamedTemporaryFile(prefix='db-backup-blocker-', delete=False)
    blocker.write(b'x')
    blocker.close()
    bad_dest = os.path.join(blocker.name, 'backups')
    db.set_setting('db_backup_dest', bad_dest)
    r = app_client.post('/api/v1/system/db-backup/run')
    assert r.status_code == 500
    body = r.get_json()
    # Flat string per the Error schema; the nested {message, reason} object and
    # raw exception text no longer ship to the client.
    assert body['error'] == 'Backup failed'
    assert 'reason' not in body
    got = app_client.get('/api/v1/settings/db-backup').get_json()
    assert got['lastError']


# -- auth-exempt allowlist --

def test_routes_not_in_auth_exempt(app_client, db):
    from api import AUTH_EXEMPT_PATHS
    assert '/api/v1/settings/db-backup' not in AUTH_EXEMPT_PATHS
    assert '/api/v1/system/db-backup/run' not in AUTH_EXEMPT_PATHS
