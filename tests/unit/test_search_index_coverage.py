"""search_index coverage: every episode status must be indexed, not just processed.

rebuild_search_index and index_episode used to filter on status='processed', so a
discovered/pending episode was invisible to search until it finished processing.
These tests pin: (1) every status is indexed and searchable by title/description/
transcript, and (2) the bulk discovery path indexes the whole batch in one
transaction rather than committing per row (issue: refresh already logs 5-13s
write-lock holds; N per-row commits would make that worse).
"""

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('search_index_coverage_')

import database

db = database.Database()

_counter = [0]


def _eid() -> str:
    # 'c' prefix keeps this module's ids disjoint from test_search_grouped's,
    # which writes through get_database() and may land in this module's DB.
    _counter[0] += 1
    return f"c{_counter[0]:011x}"


def _episode(ep_id, title=None, description=None, published='2026-01-01T00:00:00Z'):
    return {
        'id': ep_id,
        'title': title or f'Episode {ep_id}',
        'description': description,
        'published': published,
        'url': f'https://example.com/{ep_id}.mp3',
    }


def _feed(slug):
    db.create_podcast(slug, f'https://example.com/{slug}.xml', 'The Daily Tech Show')
    return slug


def _episode_hit(results, ep_id):
    return any(r['type'] == 'episode' and r['id'] == ep_id for r in results)


def test_discovered_episode_findable_by_title():
    slug = _feed('discovered-title')
    ep_id = _eid()
    db.bulk_upsert_discovered_episodes(slug, [_episode(ep_id, title='Quixotic Marmalade Hour')])
    assert _episode_hit(db.search('Quixotic'), ep_id)


def test_discovered_episode_findable_by_description():
    slug = _feed('discovered-desc')
    ep_id = _eid()
    ep = _episode(ep_id, description='a deep dive into glorbnorf farming techniques')
    db.bulk_upsert_discovered_episodes(slug, [ep])
    assert _episode_hit(db.search('glorbnorf'), ep_id)


def test_processed_episode_still_findable_by_transcript():
    slug = _feed('processed-transcript')
    ep_id = _eid()
    db.bulk_upsert_discovered_episodes(slug, [_episode(ep_id, title='Zylophraxis Show')])
    db.upsert_episode(slug, ep_id, status='processed')
    db.save_episode_details(slug, ep_id, transcript_text='mentions targaryen dragons extensively')
    db.index_episode(ep_id, slug)
    assert _episode_hit(db.search('targaryen'), ep_id)


def test_upsert_episode_new_row_is_indexed_immediately():
    slug = _feed('upsert-new-row')
    ep_id = _eid()
    db.upsert_episode(slug, ep_id, title='Vexillological Chronicles', description='flags and heraldry')
    assert _episode_hit(db.search('Vexillological'), ep_id)


def test_reindex_scopes_by_podcast_slug_not_bare_episode_id():
    """Two podcasts can share an episode_id GUID; reindexing one must not lose the other's row.

    index_episodes scopes its DELETE/SELECT by (episode_id, podcast_slug) pairs rather than a
    bare episode_id, precisely so this case is safe: a naive bare-episode_id DELETE would sweep
    up both podcasts' search_index rows, while a properly-scoped SELECT would only re-fetch and
    reinsert the one requested, permanently dropping the other podcast's row from the index.
    """
    slug_a = _feed('collide-a')
    slug_b = _feed('collide-b')
    shared_id = _eid()
    db.bulk_upsert_discovered_episodes(slug_a, [_episode(shared_id, title='Umbraflux Podcast A')])
    db.bulk_upsert_discovered_episodes(slug_b, [_episode(shared_id, title='Umbraflux Podcast B')])

    conn = db.get_connection()
    count_sql = "SELECT COUNT(*) FROM search_index WHERE content_type = 'episode' AND content_id = ?"
    assert conn.execute(count_sql, (shared_id,)).fetchone()[0] == 2

    assert db.index_episode(shared_id, slug_a) is True

    assert conn.execute(count_sql, (shared_id,)).fetchone()[0] == 2
    rows = conn.execute(
        "SELECT podcast_slug, title FROM search_index "
        "WHERE content_type = 'episode' AND content_id = ?",
        (shared_id,)
    ).fetchall()
    by_slug = {r['podcast_slug']: r['title'] for r in rows}
    assert by_slug[slug_a] == 'Umbraflux Podcast A'
    assert by_slug[slug_b] == 'Umbraflux Podcast B'


def test_bulk_insert_uses_one_batched_index_call_not_per_row(monkeypatch):
    slug = _feed('bulk-index-batch')
    real_transaction = db.transaction

    class CommitCountingConn:
        """Proxies the transaction's connection to prove index_episodes never commits it."""

        def __init__(self, conn):
            self._conn = conn
            self.commit_calls = 0

        def commit(self):
            self.commit_calls += 1
            return self._conn.commit()

        def __getattr__(self, name):
            return getattr(self._conn, name)

    wrapped = {}

    class WrappedTransaction:
        def __init__(self, immediate=False):
            self._ctx = real_transaction(immediate=immediate)

        def __enter__(self):
            conn = CommitCountingConn(self._ctx.__enter__())
            wrapped['conn'] = conn
            return conn

        def __exit__(self, *exc):
            return self._ctx.__exit__(*exc)

    monkeypatch.setattr(db, 'transaction', WrappedTransaction)

    index_calls = []
    real_index_episodes = db.index_episodes

    def spy_index_episodes(pairs, conn=None):
        index_calls.append((len(pairs), conn is not None))
        return real_index_episodes(pairs, conn=conn)

    monkeypatch.setattr(db, 'index_episodes', spy_index_episodes)

    single_calls = []
    monkeypatch.setattr(db, 'index_episode', lambda *a, **k: single_calls.append(a))

    episodes = [_episode(_eid()) for _ in range(5)]
    inserted = db.bulk_upsert_discovered_episodes(slug, episodes)

    assert inserted == 5
    assert index_calls == [(5, True)]
    assert single_calls == []
    assert wrapped['conn'].commit_calls == 0


def test_reindex_migration_runs_once(monkeypatch):
    conn = db.get_connection()
    conn.execute(
        "DELETE FROM schema_migrations WHERE name = 'reindex_search_all_episode_statuses'"
    )
    conn.commit()

    calls = []
    real_rebuild = db.rebuild_search_index

    def spy_rebuild():
        calls.append(1)
        return real_rebuild()

    monkeypatch.setattr(db, 'rebuild_search_index', spy_rebuild)

    db._run_reindex_search_all_episode_statuses(conn)
    db._run_reindex_search_all_episode_statuses(conn)

    assert calls == [1]
    row = conn.execute(
        "SELECT 1 FROM schema_migrations WHERE name = 'reindex_search_all_episode_statuses'"
    ).fetchone()
    assert row is not None


def test_reindex_migration_skips_rebuild_when_already_populated(monkeypatch):
    """A fresh install's empty-index auto-populate already rebuilt this boot; don't redo it."""
    conn = db.get_connection()
    conn.execute(
        "DELETE FROM schema_migrations WHERE name = 'reindex_search_all_episode_statuses'"
    )
    conn.commit()

    calls = []
    monkeypatch.setattr(db, 'rebuild_search_index', lambda: calls.append(1))

    db._run_reindex_search_all_episode_statuses(conn, already_rebuilt=True)

    assert calls == []
    row = conn.execute(
        "SELECT 1 FROM schema_migrations WHERE name = 'reindex_search_all_episode_statuses'"
    ).fetchone()
    assert row is not None


def test_reindex_delete_does_not_scan_the_fts_table():
    """`(content_id, podcast_slug) IN (VALUES ...)` makes FTS5 visit every stored row,
    so a single-episode reindex costs the whole table. The DELETE must go by rowid."""
    slug = _feed('delete-plan')
    ep_id = _eid()
    db.bulk_upsert_discovered_episodes(slug, [_episode(ep_id)])

    conn = db.get_connection()
    statements = []
    conn.set_trace_callback(statements.append)
    try:
        db.index_episode(ep_id, slug)
    finally:
        conn.set_trace_callback(None)

    deletes = [s for s in statements
               if s.lstrip().upper().startswith('DELETE') and 'search_index' in s]
    assert deletes, 'index_episodes issued no DELETE against search_index'
    for sql in deletes:
        plan = conn.execute('EXPLAIN QUERY PLAN ' + sql).fetchall()
        # FTS5 names the constraint it accepted after "INDEX 0:"; an empty one means it
        # took nothing and walks the whole table.
        assert not any(row['detail'].rstrip().endswith('VIRTUAL TABLE INDEX 0:')
                       for row in plan), [row['detail'] for row in plan]


def test_index_episodes_chunks_beyond_the_sql_variable_limit():
    """One statement per 500 pairs: an unchunked VALUES list blows SQLite's
    variable limit and the swallowed error leaves the episodes unindexed."""
    slug = _feed('chunked-index')
    real_ids = [_eid() for _ in range(3)]
    db.bulk_upsert_discovered_episodes(
        slug, [_episode(i, title=f'Perihelion Chronicle {i}') for i in real_ids])

    pairs = [(i, slug) for i in real_ids] + [(f'ghost{n:011x}', slug) for n in range(17000)]
    assert db.index_episodes(pairs) == 3
    for ep_id in real_ids:
        assert any(e['episodeId'] == ep_id
                   for e in db.search_grouped('Perihelion')['episodes'])


def test_guid_change_moves_the_index_row_to_the_new_id():
    """A discovered episode whose feed reissues it under a new GUID keeps one index
    row, under the new id: the old row would link to an episode that no longer exists."""
    slug = _feed('guid-change')
    old_id, new_id = _eid(), _eid()
    ep = _episode(old_id, title='Peregrine Almanac', published='2026-02-02T00:00:00Z')
    db.bulk_upsert_discovered_episodes(slug, [ep])
    db.bulk_upsert_discovered_episodes(slug, [dict(ep, id=new_id)])

    conn = db.get_connection()
    ids = [r['content_id'] for r in conn.execute(
        "SELECT content_id FROM search_index WHERE content_type = 'episode' "
        "AND podcast_slug = ? AND content_id IN (?, ?)", (slug, old_id, new_id)).fetchall()]
    assert ids == [new_id]
    assert any(e['episodeId'] == new_id
               for e in db.search_grouped('Peregrine')['episodes'])


def test_upsert_new_row_indexes_inside_the_insert_transaction(monkeypatch):
    """The insert used to commit and then index, opening a second write transaction
    per new episode."""
    slug = _feed('upsert-one-commit')
    ep_id = _eid()

    class CommitCountingConn:
        def __init__(self, conn):
            self._conn = conn
            self.commit_calls = 0

        def commit(self):
            self.commit_calls += 1
            return self._conn.commit()

        def __getattr__(self, name):
            return getattr(self._conn, name)

    proxy = CommitCountingConn(db.get_connection())
    monkeypatch.setattr(db, 'get_connection', lambda: proxy)
    db.upsert_episode(slug, ep_id, title='Solstice Reverie')

    assert proxy.commit_calls == 1
    assert any(e['episodeId'] == ep_id for e in db.search_grouped('Solstice')['episodes'])
