"""Discovered-episode upsert under write contention.

bulk_upsert_discovered_episodes ran on a bare connection, so Python's default
deferred transaction upgraded to a write lock at the first INSERT. That upgrade
returns SQLITE_BUSY immediately instead of waiting on busy_timeout, and the
per-episode except swallowed it, so a contended refresh reported success having
discovered nothing.
"""

import sqlite3

import pytest

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('bulk_upsert_test_')

import database

db = database.Database()

_counter = [0]


def _eid() -> str:
    _counter[0] += 1
    return f"{_counter[0]:012x}"


def _episode(ep_id, title=None, published='2026-01-01T00:00:00Z'):
    """Title defaults to one derived from the id: episodes sharing a title and
    published date are treated as a GUID change of the same episode and
    deliberately skipped, which would mask what these tests measure."""
    return {
        'id': ep_id,
        'title': title or f'Episode {ep_id}',
        'published': published,
        'url': f'https://example.com/{ep_id}.mp3',
    }


def _feed(slug):
    db.create_podcast(slug, f'https://example.com/{slug}.xml', 'The Daily Tech Show')
    return slug


def test_inserts_new_episodes_and_counts_them():
    slug = _feed('upsert-counts')
    inserted = db.bulk_upsert_discovered_episodes(
        slug, [_episode(_eid()), _episode(_eid())])
    assert inserted == 2


def test_reupsert_of_same_guids_counts_zero_new():
    slug = _feed('upsert-idempotent')
    ep = _episode(_eid())
    db.bulk_upsert_discovered_episodes(slug, [ep])
    assert db.bulk_upsert_discovered_episodes(slug, [ep]) == 0


def test_unknown_slug_returns_zero():
    assert db.bulk_upsert_discovered_episodes('no-such-feed', [_episode(_eid())]) == 0


def test_lock_contention_raises_instead_of_dropping_episodes(monkeypatch):
    """A busy database must fail the batch so the caller retries the feed,
    rather than returning a count that looks like a successful refresh."""
    slug = _feed('upsert-contended')

    def busy_transaction(immediate=False):
        raise sqlite3.OperationalError('database is locked')

    monkeypatch.setattr(db, 'transaction', busy_transaction)
    with pytest.raises(sqlite3.OperationalError):
        db.bulk_upsert_discovered_episodes(slug, [_episode(_eid())])


def test_lock_error_inside_the_loop_aborts_the_batch(monkeypatch):
    """A lock that surfaces on an individual INSERT is batch-fatal too: the
    remaining episodes would silently go missing otherwise."""
    slug = _feed('upsert-midloop')
    real_transaction = db.transaction

    class BusyOnExecute:
        def __init__(self, conn):
            self._conn = conn

        def execute(self, sql, *args):
            if sql.lstrip().upper().startswith('INSERT INTO EPISODES'):
                raise sqlite3.OperationalError('database is locked')
            return self._conn.execute(sql, *args)

        def __getattr__(self, name):
            return getattr(self._conn, name)

    class WrappedTransaction:
        def __init__(self, immediate=False):
            self._ctx = real_transaction(immediate=immediate)

        def __enter__(self):
            return BusyOnExecute(self._ctx.__enter__())

        def __exit__(self, *exc):
            return self._ctx.__exit__(*exc)

    monkeypatch.setattr(db, 'transaction', WrappedTransaction)
    with pytest.raises(sqlite3.OperationalError):
        db.bulk_upsert_discovered_episodes(slug, [_episode(_eid()), _episode(_eid())])


def test_malformed_row_is_skipped_without_failing_the_batch():
    """A per-row data fault must not abort the whole feed; only lock failures do."""
    slug = _feed('upsert-malformed')
    episodes = [_episode(_eid()), {}, _episode(_eid())]
    assert db.bulk_upsert_discovered_episodes(slug, episodes) == 2


def test_batch_is_committed_and_readable_afterwards():
    slug = _feed('upsert-committed')
    ep_id = _eid()
    db.bulk_upsert_discovered_episodes(slug, [_episode(ep_id, title='Episode 1')])
    stored = db.get_episode(slug, ep_id)
    assert stored is not None
    assert stored['status'] == 'discovered'
