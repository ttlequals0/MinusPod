import os
import subprocess
import sys
from pathlib import Path

import pytest

from maintenance_lock import (
    ServiceRunningError, acquire_offline_lock, release_lock,
)
from scripts.restore_backup import restore
from secrets_crypto import decrypt, encrypt, reset_cache, rotate


def test_offline_maintenance_refuses_live_worker(tmp_path):
    code = (
        'import sys,time; sys.path.insert(0,"src"); '
        'from maintenance_lock import acquire_runtime_lock; '
        f'acquire_runtime_lock({str(tmp_path)!r}); print("ready",flush=True); time.sleep(5)'
    )
    worker = subprocess.Popen(
        [sys.executable, '-c', code], cwd=os.getcwd(), stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert worker.stdout.readline().strip() == 'ready'
        with pytest.raises(ServiceRunningError):
            acquire_offline_lock(tmp_path)
    finally:
        worker.terminate()
        worker.wait(timeout=2)


def test_cold_rotation_verifies_new_key_and_backup_restores(temp_db, monkeypatch, tmp_path):
    monkeypatch.setenv('MINUSPOD_MASTER_PASSPHRASE', 'old-passphrase')
    reset_cache()
    temp_db.set_setting('anthropic_api_key', encrypt(temp_db, 'secret-value'))

    lock_fd = acquire_offline_lock(temp_db.data_dir)
    try:
        assert rotate(temp_db, 'old-passphrase', 'new-passphrase') == 1
    finally:
        release_lock(lock_fd)
    monkeypatch.setenv('MINUSPOD_MASTER_PASSPHRASE', 'new-passphrase')
    reset_cache()
    assert decrypt(temp_db, temp_db.get_setting('anthropic_api_key')) == 'secret-value'

    backups = sorted((Path(temp_db.data_dir) / 'backups').glob('pre-secret-migration-*.db'))
    restored = tmp_path / 'prepared-restore.db'
    restore(backups[-1], restored, None)
    assert restored.exists()
