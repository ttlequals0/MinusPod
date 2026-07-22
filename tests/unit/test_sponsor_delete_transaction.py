"""Regression tests for issue #566: a failed sponsor delete must not leak
an open transaction on the thread-local connection, and the request
teardown hook must clear one if any write path ever does."""
import sqlite3

import pytest

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('sponsor_del_tx_')


def _make_sponsor_with_pattern(temp_db):
    sponsor_id = temp_db.create_known_sponsor('Acme Widgets')
    temp_db.create_ad_pattern(
        scope='global',
        text_template='This episode is brought to you by Acme Widgets, the '
                      'widget company trusted by podcast hosts everywhere.',
        sponsor_id=sponsor_id,
    )
    return sponsor_id


def test_hard_delete_unlinks_and_deletes(temp_db):
    sponsor_id = _make_sponsor_with_pattern(temp_db)
    deleted, unlinked = temp_db.hard_delete_known_sponsor(sponsor_id)
    assert deleted is True
    assert unlinked == 1
    assert not temp_db.get_connection().in_transaction


def test_hard_delete_under_lock_raises_but_leaves_no_open_transaction(temp_db):
    sponsor_id = _make_sponsor_with_pattern(temp_db)
    conn = temp_db.get_connection()
    conn.execute("PRAGMA busy_timeout = 100")

    blocker = sqlite3.connect(str(temp_db.db_path))
    try:
        blocker.execute("BEGIN IMMEDIATE")
        with pytest.raises(sqlite3.OperationalError):
            temp_db.hard_delete_known_sponsor(sponsor_id)
        # The failure must not leave the write transaction open: that is
        # exactly the state that froze every later write in issue #566.
        assert not conn.in_transaction
    finally:
        blocker.rollback()
        blocker.close()
        conn.execute("PRAGMA busy_timeout = 30000")

    # The connection stays usable and the delete succeeds on retry.
    deleted, unlinked = temp_db.hard_delete_known_sponsor(sponsor_id)
    assert deleted is True
    assert unlinked == 1


def test_transaction_immediate_takes_write_lock_up_front(temp_db):
    with temp_db.transaction(immediate=True) as conn:
        assert conn.in_transaction
        blocker = sqlite3.connect(str(temp_db.db_path))
        try:
            blocker.execute("PRAGMA busy_timeout = 100")
            with pytest.raises(sqlite3.OperationalError):
                blocker.execute("BEGIN IMMEDIATE")
        finally:
            blocker.close()
    assert not conn.in_transaction


def test_rollback_open_transaction_clears_leaked_transaction(temp_db):
    conn = temp_db.get_connection()
    conn.execute("BEGIN IMMEDIATE")
    assert conn.in_transaction
    assert temp_db.rollback_open_transaction() is True
    assert not conn.in_transaction
    assert temp_db.rollback_open_transaction() is False


def test_rollback_open_transaction_does_not_create_connection(temp_db):
    import threading

    results = {}

    def check():
        # Fresh thread: no thread-local connection exists yet.
        results['rolled_back'] = temp_db.rollback_open_transaction()
        results['has_conn'] = getattr(temp_db._local, 'connection', None) is not None

    t = threading.Thread(target=check)
    t.start()
    t.join()
    assert results['rolled_back'] is False
    assert results['has_conn'] is False


def test_teardown_hook_rolls_back_leaked_transaction(temp_db, monkeypatch):
    from main_app import _rollback_leaked_transaction
    import main_app

    monkeypatch.setattr(main_app, 'db', temp_db)
    conn = temp_db.get_connection()
    conn.execute("BEGIN IMMEDIATE")

    from main_app import app
    with app.test_request_context('/api/v1/settings/retention', method='PUT'):
        _rollback_leaked_transaction(None)

    assert not conn.in_transaction
