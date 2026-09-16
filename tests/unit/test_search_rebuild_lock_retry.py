"""Characterization tests of the search index rebuild: one locked attempt,
chunked corpus writes, shadow-table cleanup, shared busy timeout."""
import sqlite3
from unittest.mock import patch

import pytest

import database.search as search


def test_a_locked_rebuild_propagates_instead_of_retrying_under_the_lock(temp_db):
    """The background caller backs off a cycle; retrying here would redo the
    whole corpus while still holding the process-shared rebuild lock."""
    attempts = []

    def always_locked(self):
        attempts.append(1)
        raise sqlite3.OperationalError('database is locked')

    with patch.object(search.SearchMixin, '_rebuild_search_index_locked', always_locked):
        with pytest.raises(sqlite3.OperationalError):
            temp_db.rebuild_search_index()
    assert len(attempts) == 1


def test_a_successful_rebuild_returns_its_count(temp_db):
    with patch.object(search.SearchMixin, '_rebuild_search_index_locked', lambda self: 7):
        assert temp_db.rebuild_search_index() == 7


def test_a_failed_rebuild_drops_its_shadow_table(temp_db):
    import os
    import threading
    shadow = f"{search._SHADOW_PREFIX}_{os.getpid()}_{threading.get_ident()}"
    conn = temp_db.get_connection()
    conn.execute(search.SEARCH_INDEX_DDL.format(name=shadow))
    conn.commit()

    def boom(self):
        raise sqlite3.OperationalError('database is locked')

    with patch.object(search.SearchMixin, '_rebuild_search_index_locked', boom):
        with pytest.raises(sqlite3.OperationalError):
            temp_db.rebuild_search_index()
    assert conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (shadow,)).fetchone() is None


def test_rebuild_writes_the_corpus_in_bounded_chunks(temp_db, monkeypatch):
    """Chunked writes, not a retry, are what bound the rebuild's lock hold."""
    rows = search._REBUILD_TX_ROWS * 2 + 1
    for i in range(rows):
        temp_db.create_podcast(f'chunk-feed-{i}', f'https://example.com/{i}.xml',
                               title=f'Chunk Feed {i}')
    real_transaction = temp_db.transaction
    opened = []

    def counting_transaction(immediate=False):
        opened.append(immediate)
        return real_transaction(immediate=immediate)

    monkeypatch.setattr(temp_db, 'transaction', counting_transaction)
    temp_db.rebuild_search_index()
    # One to create the shadow, one per chunk of rows, one to swap it in. Tied
    # to the row count so a rewrite that writes the corpus in one transaction
    # cannot satisfy it.
    chunks = -(-rows // search._REBUILD_TX_ROWS)
    assert len(opened) >= chunks + 2


def test_connections_apply_the_shared_busy_timeout(temp_db):
    from database import BUSY_TIMEOUT_MS

    pragma = temp_db.get_connection().execute('PRAGMA busy_timeout').fetchone()[0]
    # The literal too: a self-consistency check alone would pass on a timeout
    # quietly dropped to milliseconds.
    assert pragma == 60000
    assert pragma == BUSY_TIMEOUT_MS
