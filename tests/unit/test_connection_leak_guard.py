"""A failing statement must not leave the thread's connection in a transaction (#566)."""
import sqlite3

import pytest

from tests.app_bootstrap import bootstrap

bootstrap('connection_leak_guard_test_')


def _block(temp_db):
    conn = temp_db.get_connection()
    conn.execute("PRAGMA busy_timeout = 100")
    blocker = sqlite3.connect(str(temp_db.db_path))
    blocker.execute("BEGIN IMMEDIATE")
    return conn, blocker


def _release(conn, blocker):
    blocker.rollback()
    blocker.close()
    conn.execute("PRAGMA busy_timeout = 30000")


def test_bare_writer_that_hits_the_lock_leaves_no_open_transaction(temp_db, mock_episode):
    slug, ep = mock_episode['slug'], mock_episode['episode_id']
    conn, blocker = _block(temp_db)
    try:
        with pytest.raises(sqlite3.OperationalError):
            temp_db.save_original_transcript(slug, ep, 'hello world')
        assert not conn.in_transaction
    finally:
        _release(conn, blocker)
    temp_db.save_original_transcript(slug, ep, 'hello world')
    assert temp_db.get_original_transcript(slug, ep) == 'hello world'


def test_failed_statement_inside_a_caller_transaction_is_left_to_the_caller(temp_db):
    conn = temp_db.get_connection()
    with pytest.raises(sqlite3.OperationalError):
        with temp_db.transaction() as tx:
            tx.execute("INSERT INTO settings (key, value) VALUES ('k', 'v')")
            tx.execute("INSERT INTO no_such_table VALUES (1)")
    assert not conn.in_transaction
    assert temp_db.get_setting('k') is None


def test_begin_immediate_rolls_back_a_leaked_transaction(temp_db, caplog):
    conn = temp_db.get_connection()
    conn.execute("BEGIN")
    conn.execute("INSERT INTO settings (key, value) VALUES ('leaked', '1')")
    assert conn.in_transaction
    with temp_db.transaction(immediate=True) as tx:
        tx.execute("INSERT INTO settings (key, value) VALUES ('fresh', '1')")
    assert not conn.in_transaction
    assert temp_db.get_setting('leaked') is None
    assert temp_db.get_setting('fresh') == '1'
    assert any('leaked transaction' in r.getMessage() for r in caplog.records)
