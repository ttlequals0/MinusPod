"""TracedConnection names the holder of a long write transaction and slow lock waits."""
import logging
import sqlite3
import time

import pytest

import database
from database import TracedConnection


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
