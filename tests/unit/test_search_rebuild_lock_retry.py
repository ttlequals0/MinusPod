"""Characterization tests of the search index rebuild: one locked attempt,
chunked corpus writes, shadow-table cleanup, shared busy timeout."""
import sqlite3
import threading
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


def test_rebuild_retries_a_transient_chunk_lock(temp_db, monkeypatch):
    real = temp_db.transaction
    attempts = {'count': 0}

    class _Context:
        def __init__(self, ctx):
            self.ctx = ctx

        def __enter__(self):
            attempts['count'] += 1
            if attempts['count'] == 2:
                raise sqlite3.OperationalError('database is locked')
            return self.ctx.__enter__()

        def __exit__(self, *args):
            return self.ctx.__exit__(*args)

    monkeypatch.setattr(temp_db, 'transaction',
                        lambda immediate=False: _Context(real(immediate=immediate)))
    monkeypatch.setattr(search.time, 'sleep', lambda _seconds: None)
    assert temp_db.rebuild_search_index() >= 0
    assert attempts['count'] >= 4


def test_rebuild_allows_a_commit_between_source_batches(temp_db, monkeypatch):
    """A source read must not leave a stale snapshot for the batch write."""
    monkeypatch.setattr(search, '_REBUILD_TX_ROWS', 1)
    temp_db.create_podcast('initial-feed', 'https://example.com/initial.xml', title='Initial')
    temp_db.create_podcast('second-feed', 'https://example.com/second.xml', title='Second')
    real_run = temp_db._run_rebuild_write
    write_attempts = {'count': 0}
    writer_errors = []

    def run_with_concurrent_writer(operation):
        write_attempts['count'] += 1
        if write_attempts['count'] != 2:
            return real_run(operation)

        def write_feed():
            conn = sqlite3.connect(str(temp_db.db_path), timeout=5)
            try:
                conn.execute(
                    "INSERT INTO podcasts (slug, source_url, title, own_episode_guids, feed_type) "
                    "VALUES (?, ?, ?, 1, ?)",
                    ('concurrent-feed', 'https://example.com/concurrent.xml', 'Concurrent', 'subscribed'),
                )
                conn.commit()
            except Exception as exc:
                writer_errors.append(exc)
            finally:
                conn.close()

        writer = threading.Thread(target=write_feed)
        writer.start()
        writer.join(timeout=5)
        assert not writer.is_alive()
        assert writer_errors == []
        return real_run(operation)

    monkeypatch.setattr(temp_db, '_run_rebuild_write', run_with_concurrent_writer)
    assert temp_db.rebuild_search_index() >= 3
    conn = temp_db.get_connection()
    assert conn.execute(
        "SELECT COUNT(*) FROM search_index WHERE content_type = 'podcast' "
        "AND content_id = 'concurrent-feed'"
    ).fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM search_index_changes").fetchone()[0] == 0


def _build_shadow(temp_db):
    """A shadow index with one row, ready for _swap_in_shadow to rename in."""
    import os
    import threading
    shadow = f"{search._SHADOW_PREFIX}_{os.getpid()}_{threading.get_ident()}"
    conn = temp_db.get_connection()
    conn.execute(search.SEARCH_INDEX_DDL.format(name=shadow))
    conn.execute(
        f'INSERT INTO "{shadow}" '  # noqa: S608
        '(content_type, content_id, podcast_slug, title, body, metadata) '
        'VALUES (?, ?, ?, ?, ?, ?)',
        ('podcast', 'swap-feed', 'swap-feed', 'Swap Feed', '', ''))
    conn.commit()
    return shadow, conn


def _flaky_swap(temp_db, monkeypatch, *, locked_attempts, error='database is locked'):
    """Raise `error` on the first `locked_attempts` transaction enters, then
    delegate to the real transaction. _swap_in_shadow opens one transaction per
    attempt, so n['i'] is the swap attempt count."""
    real = temp_db.transaction
    n = {'i': 0}

    class _Ctx:
        def __init__(self, r):
            self.r = r

        def __enter__(self):
            n['i'] += 1
            if n['i'] <= locked_attempts:
                raise sqlite3.OperationalError(error)
            return self.r.__enter__()

        def __exit__(self, *a):
            return self.r.__exit__(*a)

    monkeypatch.setattr(temp_db, 'transaction',
                        lambda immediate=False: _Ctx(real(immediate=immediate)))
    monkeypatch.setattr(search.time, 'sleep', lambda _s: None)
    return n


def test_the_swap_retries_on_a_locked_database_then_commits(temp_db, monkeypatch):
    shadow, conn = _build_shadow(temp_db)
    insert = (f'INSERT INTO "{shadow}" '  # noqa: S608
              '(content_type, content_id, podcast_slug, title, body, metadata) '
              'VALUES (?, ?, ?, ?, ?, ?)')
    n = _flaky_swap(temp_db, monkeypatch, locked_attempts=1)

    temp_db._swap_in_shadow(shadow, insert, 0)

    # 1 locked attempt + 1 committed swap, then the retired-table purge opens
    # its own short transactions (one empty delete, one drop).
    assert n['i'] == 4
    # The shadow (carrying swap-feed) is now the live index.
    assert conn.execute(
        "SELECT COUNT(*) FROM search_index WHERE content_id = 'swap-feed'"
    ).fetchone()[0] == 1
    # The renamed-aside original is fully purged, not left behind.
    assert conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name GLOB ?",
        (f"{search._RETIRED_PREFIX}_[0-9]*_[0-9]*",)).fetchone() is None


def test_the_swap_gives_up_after_exhausting_its_retries(temp_db, monkeypatch):
    shadow, _ = _build_shadow(temp_db)
    n = _flaky_swap(temp_db, monkeypatch, locked_attempts=search._SWAP_LOCK_RETRIES)

    with pytest.raises(sqlite3.OperationalError):
        temp_db._swap_in_shadow(shadow, 'INSERT INTO x VALUES (1)', 0)
    assert n['i'] == search._SWAP_LOCK_RETRIES


def test_a_non_lock_error_in_the_swap_is_not_retried(temp_db, monkeypatch):
    shadow, _ = _build_shadow(temp_db)
    n = _flaky_swap(temp_db, monkeypatch, locked_attempts=1, error='no such column')

    with pytest.raises(sqlite3.OperationalError, match='no such column'):
        temp_db._swap_in_shadow(shadow, 'INSERT INTO x VALUES (1)', 0)
    assert n['i'] == 1


def test_connections_apply_the_shared_busy_timeout(temp_db):
    from database import BUSY_TIMEOUT_MS

    pragma = temp_db.get_connection().execute('PRAGMA busy_timeout').fetchone()[0]
    # The literal too: a self-consistency check alone would pass on a timeout
    # quietly dropped to milliseconds.
    assert pragma == 60000
    assert pragma == BUSY_TIMEOUT_MS
