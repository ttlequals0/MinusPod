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
    monkeypatch.setattr(bg, '_wal_bytes', lambda: bg.WAL_TRUNCATE_MIN_BYTES)
    return calls


def test_tick_skips_a_small_wal(monkeypatch):
    calls = _record(monkeypatch)
    monkeypatch.setattr(bg, '_wal_bytes', lambda: bg.WAL_TRUNCATE_MIN_BYTES - 1)
    monkeypatch.setattr(bg, 'backup_in_progress', lambda _db: False)
    bg._maybe_checkpoint_wal(_registry(0))
    assert calls == []


def test_wal_bytes_reads_the_wal_file(monkeypatch, tmp_path):
    monkeypatch.setattr(bg.db, 'db_path', tmp_path / 'x.db')
    assert bg._wal_bytes() == 0
    (tmp_path / 'x.db-wal').write_bytes(b'x' * 10)
    assert bg._wal_bytes() == 10


def test_tick_runs_truncate_checkpoint_when_idle(monkeypatch):
    calls = _record(monkeypatch)
    monkeypatch.setattr(bg, 'backup_in_progress', lambda _db: False)
    bg._maybe_checkpoint_wal(_registry(0))
    assert calls == ['truncate']


def test_successful_truncate_logs_the_page_counts(monkeypatch, caplog):
    _record(monkeypatch)
    monkeypatch.setattr(bg, 'backup_in_progress', lambda _db: False)
    with caplog.at_level('DEBUG', logger='podcast.refresh'):
        bg._maybe_checkpoint_wal(_registry(0))
    assert any('log_pages=0' in r.getMessage() for r in caplog.records)


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


def test_backup_in_progress_ignores_another_shared_probe(tmp_path):
    fake_db = types.SimpleNamespace(data_dir=str(tmp_path))
    with open(tmp_path / LOCK_FILENAME, 'w') as probe:
        fcntl.flock(probe, fcntl.LOCK_SH | fcntl.LOCK_NB)
        assert backup_in_progress(fake_db) is False


def test_backup_in_progress_is_false_when_the_lock_cannot_be_opened(tmp_path):
    fake_db = types.SimpleNamespace(data_dir=str(tmp_path / 'missing'))
    assert backup_in_progress(fake_db) is False
