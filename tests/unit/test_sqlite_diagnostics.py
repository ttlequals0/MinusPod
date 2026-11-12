import sqlite3
import time

import pytest

from database import BUSY_TIMEOUT_MS, Database


def test_sqlite_diagnostics_reports_scoped_metrics_without_path(tmp_path):
    db = Database(tmp_path)
    diagnostics = db.sqlite_diagnostics()

    assert diagnostics['journalMode'] == 'wal'
    assert diagnostics['databaseBytes'] > 0
    assert diagnostics['walBytes'] >= 0
    assert diagnostics['instrumentation']['scope'] == 'worker_process'
    assert diagnostics['instrumentation']['processId'] > 0
    assert str(tmp_path) not in repr(diagnostics)


def test_passive_checkpoint_returns_sqlite_counts(tmp_path):
    db = Database(tmp_path)
    db.get_connection().execute('CREATE TABLE IF NOT EXISTS checkpoint_probe (x INTEGER)')
    db.get_connection().execute('INSERT INTO checkpoint_probe VALUES (1)')
    db.get_connection().commit()

    result = db.checkpoint_wal()

    assert set(result) == {'mode', 'busy', 'logPages', 'checkpointedPages', 'durationMs'}
    assert result['mode'] == 'passive'
    assert result['busy'] in (0, 1)
    assert result['logPages'] >= 0
    assert result['checkpointedPages'] >= 0
    assert result['durationMs'] >= 0


def test_journal_size_limit_is_64mb_on_a_new_connection(tmp_path):
    db = Database(tmp_path)
    limit = db.get_connection().execute('PRAGMA journal_size_limit').fetchone()[0]
    assert limit == 64 * 1024 * 1024


def test_truncate_checkpoint_reports_its_mode(tmp_path):
    db = Database(tmp_path)
    conn = db.get_connection()
    conn.execute('CREATE TABLE mode_probe (x)')
    conn.commit()
    try:
        result = db.checkpoint_wal(mode='truncate')
    finally:
        conn.execute('DROP TABLE IF EXISTS mode_probe')
        conn.commit()
    assert result['mode'] == 'truncate'
    assert set(result) >= {'busy', 'logPages', 'checkpointedPages', 'durationMs', 'mode'}


def test_truncate_checkpoint_empties_the_wal_file(tmp_path):
    db = Database(tmp_path)
    conn = db.get_connection()
    conn.execute('CREATE TABLE wal_fill (x)')
    try:
        conn.executemany('INSERT INTO wal_fill VALUES (?)', [('x' * 1000,)] * 200)
        conn.commit()
        wal = db.db_path.with_name(db.db_path.name + '-wal')
        assert wal.stat().st_size > 0
        db.checkpoint_wal(mode='truncate')
        assert not wal.exists() or wal.stat().st_size == 0
    finally:
        conn.execute('DROP TABLE IF EXISTS wal_fill')
        conn.commit()


def test_unknown_checkpoint_mode_raises(tmp_path):
    db = Database(tmp_path)
    with pytest.raises(ValueError):
        db.checkpoint_wal(mode='bogus')


def test_truncate_checkpoint_gives_up_quickly_when_a_reader_blocks_it(tmp_path):
    db = Database(tmp_path)
    conn = db.get_connection()
    conn.execute('CREATE TABLE IF NOT EXISTS reader_probe (x)')
    conn.execute('INSERT INTO reader_probe VALUES (1)')
    conn.commit()
    reader = sqlite3.connect(str(db.db_path))
    try:
        reader.execute('BEGIN')
        reader.execute('SELECT * FROM reader_probe').fetchall()
        conn.execute('INSERT INTO reader_probe VALUES (2)')
        conn.commit()
        started = time.monotonic()
        result = db.checkpoint_wal(mode='truncate')
        elapsed = time.monotonic() - started
    finally:
        reader.close()
        conn.execute('DROP TABLE IF EXISTS reader_probe')
        conn.commit()
    assert result['busy'] is True
    assert elapsed < 5
    assert conn.execute('PRAGMA busy_timeout').fetchone()[0] == BUSY_TIMEOUT_MS


class _FlakyConn:
    """Wraps a connection; the nth busy_timeout statement fails."""

    def __init__(self, real, fail_on, checkpoint_error=None):
        self.real, self.fail_on, self.checkpoint_error = real, fail_on, checkpoint_error
        self.timeout_calls = 0

    def execute(self, sql, *args):
        if sql.startswith('PRAGMA busy_timeout'):
            self.timeout_calls += 1
            if self.timeout_calls == self.fail_on:
                raise sqlite3.OperationalError('pragma failed')
        if 'wal_checkpoint' in sql and self.checkpoint_error:
            raise self.checkpoint_error
        return self.real.execute(sql, *args)


def test_failed_timeout_restore_does_not_mask_the_checkpoint_result(tmp_path, monkeypatch):
    db = Database(tmp_path)
    flaky = _FlakyConn(db.get_connection(), fail_on=3)
    monkeypatch.setattr(db, 'get_connection', lambda: flaky)
    assert db.checkpoint_wal(mode='truncate')['mode'] == 'truncate'


def test_failed_restore_does_not_mask_the_checkpoint_error(tmp_path, monkeypatch):
    db = Database(tmp_path)
    boom = sqlite3.OperationalError('checkpoint failed')
    flaky = _FlakyConn(db.get_connection(), fail_on=3, checkpoint_error=boom)
    monkeypatch.setattr(db, 'get_connection', lambda: flaky)
    with pytest.raises(sqlite3.OperationalError, match='checkpoint failed'):
        db.checkpoint_wal(mode='truncate')


def test_failed_timeout_read_raises_without_lowering_the_timeout(tmp_path, monkeypatch):
    db = Database(tmp_path)
    flaky = _FlakyConn(db.get_connection(), fail_on=1)
    monkeypatch.setattr(db, 'get_connection', lambda: flaky)
    with pytest.raises(sqlite3.OperationalError, match='pragma failed'):
        db.checkpoint_wal(mode='truncate')
    assert flaky.timeout_calls == 1
