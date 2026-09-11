"""TracedConnection names the holder of a long write transaction and slow lock waits."""
import logging
import sqlite3
import time

import pytest

import database
from database import TracedConnection, sqlite_metrics_snapshot


@pytest.fixture
def traced_pair(tmp_path, monkeypatch):
    monkeypatch.setattr(database, 'SLOW_SQLITE_SECONDS', 0.05)
    path = str(tmp_path / 't.db')
    holder = sqlite3.connect(path, factory=TracedConnection, timeout=0.2)
    waiter = sqlite3.connect(path, factory=TracedConnection, timeout=0.2)
    holder.execute("CREATE TABLE t (x INTEGER)")
    holder.commit()
    yield holder, waiter
    holder.close()
    waiter.close()


def test_long_held_write_transaction_is_logged_with_opener(traced_pair, caplog):
    holder, _ = traced_pair
    with caplog.at_level(logging.WARNING, logger='database'):
        holder.execute("INSERT INTO t VALUES (1)")
        time.sleep(0.08)
        holder.commit()
    assert 'write transaction held' in caplog.text
    assert 'opened by: INSERT INTO t VALUES (1)' in caplog.text


def test_short_transaction_is_quiet(traced_pair, caplog):
    holder, _ = traced_pair
    with caplog.at_level(logging.WARNING, logger='database'):
        holder.execute("INSERT INTO t VALUES (1)")
        holder.commit()
    assert caplog.text == ''


def test_lock_wait_is_logged_even_when_the_statement_fails(traced_pair, caplog):
    holder, waiter = traced_pair
    holder.execute("INSERT INTO t VALUES (1)")
    with caplog.at_level(logging.WARNING, logger='database'):
        with pytest.raises(sqlite3.OperationalError):
            waiter.execute("INSERT INTO t VALUES (2)")
    holder.rollback()
    assert 'SQLite statement took' in caplog.text
    assert 'INSERT INTO t VALUES (2)' in caplog.text


def test_failed_commit_keeps_transaction_origin_for_rollback(tmp_path):
    before = sqlite_metrics_snapshot()['failedCommits']
    conn = sqlite3.connect(tmp_path / 'deferred.db', factory=TracedConnection)
    conn.execute('PRAGMA foreign_keys = ON')
    conn.execute('CREATE TABLE parent (id INTEGER PRIMARY KEY)')
    conn.execute(
        'CREATE TABLE child (parent_id INTEGER REFERENCES parent(id) '
        'DEFERRABLE INITIALLY DEFERRED)')
    conn.commit()
    conn.execute('INSERT INTO child VALUES (1)')
    opener = conn._tx_opener

    with pytest.raises(sqlite3.IntegrityError):
        conn.commit()

    assert conn.in_transaction is True
    assert conn._tx_started is not None
    assert conn._tx_opener == opener
    assert sqlite_metrics_snapshot()['failedCommits'] == before + 1
    conn.rollback()
    assert conn._tx_started is None
    conn.close()


def test_connection_context_manager_records_commit(tmp_path):
    conn = sqlite3.connect(tmp_path / 'context.db', factory=TracedConnection)
    conn.execute('CREATE TABLE t (x INTEGER)')
    conn.commit()

    with conn:
        conn.execute('INSERT INTO t VALUES (1)')

    assert conn.in_transaction is False
    assert conn._tx_started is None
    assert sqlite_metrics_snapshot()['lastCommitMs'] >= 0
    assert conn.execute('SELECT COUNT(*) FROM t').fetchone()[0] == 1
    conn.close()


def test_connection_context_rolls_back_when_deferred_commit_fails(tmp_path):
    conn = sqlite3.connect(tmp_path / 'context-failure.db', factory=TracedConnection)
    conn.execute('PRAGMA foreign_keys = ON')
    conn.execute('CREATE TABLE parent (id INTEGER PRIMARY KEY)')
    conn.execute(
        'CREATE TABLE child (parent_id INTEGER REFERENCES parent(id) '
        'DEFERRABLE INITIALLY DEFERRED)')
    conn.commit()

    with pytest.raises(sqlite3.IntegrityError):
        with conn:
            conn.execute('INSERT INTO child VALUES (1)')

    assert conn.in_transaction is False
    assert conn._tx_started is None
    assert conn.execute('SELECT COUNT(*) FROM child').fetchone()[0] == 0
    conn.close()
