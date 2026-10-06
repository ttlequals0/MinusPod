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

    def busy_transaction(self, immediate=False):
        raise sqlite3.OperationalError('database is locked')

    monkeypatch.setattr(type(db), 'transaction', busy_transaction)
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

    monkeypatch.setattr(type(db), 'transaction', WrappedTransaction)
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


def test_same_chunk_backfill_prevents_duplicate_title_date_insert():
    slug = _feed('upsert-backfill-dedupe')
    existing_id = _eid()
    duplicate_id = _eid()
    db.upsert_episode(
        slug, existing_id, title=None, published_at=None,
        original_url='https://example.com/original.mp3', status='discovered')

    inserted = db.bulk_upsert_discovered_episodes(slug, [
        _episode(existing_id, title='Shared title'),
        _episode(duplicate_id, title='Shared title'),
    ])

    assert inserted == 0
    episodes, total = db.get_episodes(slug, status='all', limit=10)
    assert total == 1
    assert episodes[0]['episode_id'] == duplicate_id
    assert episodes[0]['title'] == 'Shared title'


@pytest.mark.parametrize('barrier', ['status', 'active_run'])
def test_guid_rename_rechecks_processing_barrier_under_writer_lock(monkeypatch, barrier):
    slug = _feed(f'upsert-guid-barrier-{barrier}')
    old_id = _eid()
    new_id = _eid()
    original = _episode(old_id, title='Shared title')
    db.bulk_upsert_discovered_episodes(slug, [original])
    real_refresh = db._refresh_discovery_state

    def refresh_then_transition(conn, podcast_id, chunk, existing_by_id, title_date_map):
        real_refresh(conn, podcast_id, chunk, existing_by_id, title_date_map)
        if barrier == 'status':
            conn.execute(
                "UPDATE episodes SET status = 'processing' "
                "WHERE podcast_id = ? AND episode_id = ?", (podcast_id, old_id))
        else:
            conn.execute(
                "INSERT INTO processing_runs "
                "(run_id, podcast_id, episode_id, owner_pid, state) "
                "VALUES ('run-guid-barrier', ?, ?, 1, 'running')",
                (podcast_id, old_id))

    monkeypatch.setattr(db, '_refresh_discovery_state', refresh_then_transition)
    db.bulk_upsert_discovered_episodes(
        slug, [_episode(new_id, title='Shared title')])

    assert db.get_episode(slug, old_id) is not None
    assert db.get_episode(slug, new_id) is None
    if barrier == 'active_run':
        conn = db.get_connection()
        conn.execute("DELETE FROM processing_runs WHERE run_id = 'run-guid-barrier'")
        conn.commit()


def test_fuzzy_match_relinks_rotated_guid_with_stale_published_at():
    """A pre-fix row's published_at can be off by a named-zone offset (up to
    14h); an upstream GUID rotation must relink to it, not insert a dup."""
    slug = _feed('upsert-fuzzy-relink')
    old_id = _eid()
    db.upsert_episode(
        slug, old_id, title='Fuzzy Relink Episode', published_at='2026-01-01T00:00:00Z',
        original_url='https://example.com/old.mp3', status='discovered')

    new_id = _eid()
    inserted = db.bulk_upsert_discovered_episodes(slug, [
        _episode(new_id, title='Fuzzy Relink Episode', published='2026-01-01T07:00:00Z'),
    ])

    assert inserted == 0
    assert db.get_episode(slug, old_id) is None
    assert db.get_episode(slug, new_id) is not None


def test_fuzzy_match_does_not_cross_a_multi_day_gap():
    """Two genuinely different episodes sharing a title days apart must both
    stay as separate rows; the fuzzy window must not swallow them."""
    slug = _feed('upsert-fuzzy-distinct')
    old_id = _eid()
    db.upsert_episode(
        slug, old_id, title='Weekly Recap', published_at='2026-01-01T00:00:00Z',
        original_url='https://example.com/old.mp3', status='discovered')

    new_id = _eid()
    inserted = db.bulk_upsert_discovered_episodes(slug, [
        _episode(new_id, title='Weekly Recap', published='2026-01-04T00:00:00Z'),
    ])

    assert inserted == 1
    assert db.get_episode(slug, old_id) is not None
    assert db.get_episode(slug, new_id) is not None


def test_fuzzy_match_prefers_completed_row_over_closer_discovered_row():
    """When both a completed and a discovered row fall in the fuzzy window,
    the completed row (the one with real state) must be matched, even when
    a discovered row sits closer in time."""
    slug = _feed('upsert-fuzzy-priority')
    discovered_id = _eid()
    completed_id = _eid()
    db.upsert_episode(
        slug, discovered_id, title='Priority Episode', published_at='2026-01-01T00:50:00Z',
        original_url='https://example.com/discovered.mp3', status='discovered')
    db.upsert_episode(
        slug, completed_id, title='Priority Episode', published_at='2026-01-01T03:00:00Z',
        original_url='https://example.com/completed.mp3', status='processed')

    new_id = _eid()
    new_ep = _episode(new_id, title='Priority Episode', published='2026-01-01T01:00:00Z')
    new_ep['episode_number'] = 42
    inserted = db.bulk_upsert_discovered_episodes(slug, [new_ep])

    assert inserted == 0
    discovered_row = db.get_episode(slug, discovered_id)
    completed_row = db.get_episode(slug, completed_id)
    assert discovered_row['episode_number'] is None
    assert completed_row['episode_number'] == 42
