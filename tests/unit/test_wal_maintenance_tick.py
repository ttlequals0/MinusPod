import fcntl
import types

from tests.app_bootstrap import bootstrap

bootstrap('wal_tick_')

import main_app.background as bg  # noqa: E402
from db_backup_service import LOCK_FILENAME, backup_in_progress  # noqa: E402


def _registry(slots):
    return types.SimpleNamespace(slot_count=lambda: slots, get_current=lambda: [])


def _record(monkeypatch, busy=0):
    calls = []

    def fake(mode='passive'):
        calls.append(mode)
        return {'busy': busy, 'logPages': 0, 'checkpointedPages': 0, 'durationMs': 0, 'mode': mode}

    monkeypatch.setattr(bg.db, 'checkpoint_wal', fake)
    return calls


def test_tick_runs_truncate_checkpoint_when_idle(monkeypatch):
    calls = _record(monkeypatch)
    monkeypatch.setattr(bg, 'backup_in_progress', lambda _db: False)
    bg._maybe_checkpoint_wal(_registry(0))
    assert calls == ['truncate']


def test_tick_skips_checkpoint_when_a_run_is_active(monkeypatch):
    calls = _record(monkeypatch)
    monkeypatch.setattr(bg, 'backup_in_progress', lambda _db: False)
    bg._maybe_checkpoint_wal(_registry(1))
    assert calls == []


def test_tick_skips_checkpoint_during_a_backup(monkeypatch):
    calls = _record(monkeypatch)
    monkeypatch.setattr(bg, 'backup_in_progress', lambda _db: True)
    bg._maybe_checkpoint_wal(_registry(0))
    assert calls == []


def test_tick_tolerates_a_busy_checkpoint(monkeypatch):
    calls = _record(monkeypatch, busy=1)
    monkeypatch.setattr(bg, 'backup_in_progress', lambda _db: False)
    bg._maybe_checkpoint_wal(_registry(0))
    assert calls == ['truncate']


def test_tick_swallows_a_checkpoint_failure(monkeypatch):
    def boom(mode='passive'):
        raise RuntimeError('database is locked')

    monkeypatch.setattr(bg.db, 'checkpoint_wal', boom)
    monkeypatch.setattr(bg, 'backup_in_progress', lambda _db: False)
    bg._maybe_checkpoint_wal(_registry(0))


def test_backup_in_progress_reflects_the_lock(tmp_path):
    fake_db = types.SimpleNamespace(data_dir=str(tmp_path))
    assert backup_in_progress(fake_db) is False
    with open(tmp_path / LOCK_FILENAME, 'w') as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert backup_in_progress(fake_db) is True
    assert backup_in_progress(fake_db) is False


def test_backup_in_progress_is_false_when_the_lock_cannot_be_opened(tmp_path):
    fake_db = types.SimpleNamespace(data_dir=str(tmp_path / 'missing'))
    assert backup_in_progress(fake_db) is False
