"""Database backup downloads fail closed unless plaintext is explicit."""
import logging
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='backup-export-test-'))
os.environ.setdefault('SECRET_KEY', 'test-secret')


def _auth(client):
    with client.session_transaction() as session:
        session['authenticated'] = True


def test_default_export_is_encrypted(app_client, monkeypatch, caplog):
    _auth(app_client)
    monkeypatch.setenv('MINUSPOD_MASTER_PASSPHRASE', 'backup-test-passphrase')

    with caplog.at_level(logging.WARNING, logger='podcast.api'):
        response = app_client.get('/api/v1/system/backup')

    assert response.status_code == 200
    assert response.mimetype == 'application/octet-stream'
    disposition = response.headers['Content-Disposition']
    assert disposition.startswith('attachment;')
    assert '.db.enc' in disposition
    assert response.data.startswith(b'MPBK02\x00')
    assert any('encrypted=True' in record.getMessage() for record in caplog.records)


def test_default_export_fails_without_passphrase(app_client, monkeypatch, caplog):
    _auth(app_client)
    monkeypatch.delenv('MINUSPOD_MASTER_PASSPHRASE', raising=False)

    with caplog.at_level(logging.WARNING, logger='podcast.api'):
        response = app_client.get('/api/v1/system/backup')

    assert response.status_code == 409
    assert response.get_json() == {
        'error': 'backup_encryption_unavailable',
        'status': 409,
    }
    assert 'MINUSPOD_MASTER_PASSPHRASE' not in response.get_data(as_text=True)
    assert any('encryption is unavailable' in record.getMessage()
               for record in caplog.records)


def test_requested_encryption_fails_without_passphrase(app_client, monkeypatch):
    _auth(app_client)
    monkeypatch.delenv('MINUSPOD_MASTER_PASSPHRASE', raising=False)

    response = app_client.get('/api/v1/system/backup?encrypted=true')

    assert response.status_code == 409
    assert response.get_json()['error'] == 'backup_encryption_unavailable'


def test_explicit_plaintext_export_is_allowed_and_audited(app_client, monkeypatch, caplog):
    _auth(app_client)
    monkeypatch.delenv('MINUSPOD_MASTER_PASSPHRASE', raising=False)

    with caplog.at_level(logging.WARNING, logger='podcast.api'):
        response = app_client.get('/api/v1/system/backup?encrypted=false')

    assert response.status_code == 200
    assert response.mimetype == 'application/octet-stream'
    disposition = response.headers['Content-Disposition']
    assert disposition.startswith('attachment;')
    assert '.db.enc' not in disposition
    assert '.db' in disposition
    assert response.data.startswith(b'SQLite format 3\x00')
    messages = [record.getMessage() for record in caplog.records]
    assert any('Plaintext database backup explicitly requested' in message
               for message in messages)
    assert any('encrypted=False' in message for message in messages)


def test_explicit_plaintext_export_overrides_configured_passphrase(app_client, monkeypatch):
    _auth(app_client)
    monkeypatch.setenv('MINUSPOD_MASTER_PASSPHRASE', 'backup-test-passphrase')

    response = app_client.get('/api/v1/system/backup?encrypted=false')

    assert response.status_code == 200
    assert response.data.startswith(b'SQLite format 3\x00')
    assert '.db.enc' not in response.headers['Content-Disposition']
