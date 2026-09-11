"""search_index coverage: every episode status must be indexed, not just processed,
since rebuild_search_index and index_episode used to filter on status='processed'."""

import os
import sqlite3
import threading

import pytest

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('search_index_coverage_')

import database

db = None


@pytest.fixture(scope='module', autouse=True)
def isolated_search_database(tmp_path_factory):
    global db
    previous = database.Database._instance
    database.Database._instance = None
    try:
        db = database.Database(data_dir=str(tmp_path_factory.mktemp('search-index-coverage')))
    finally:
        database.Database._instance = previous
    yield
    connection = getattr(db._local, 'connection', None)
    if connection is not None:
        connection.close()

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


def _episode_hit(rows, ep_id):
    return any(r['episodeId'] == ep_id for r in rows)


def test_discovered_episode_findable_by_title():
    slug = _feed('discovered-title')
    ep_id = _eid()
    db.bulk_upsert_discovered_episodes(slug, [_episode(ep_id, title='Quixotic Marmalade Hour')])
    assert _episode_hit(db.search_grouped('Quixotic')['episodes'], ep_id)


def test_discovered_episode_findable_by_description():
    slug = _feed('discovered-desc')
    ep_id = _eid()
    ep = _episode(ep_id, description='a deep dive into glorbnorf farming techniques')
    db.bulk_upsert_discovered_episodes(slug, [ep])
    assert _episode_hit(db.search_grouped('glorbnorf')['episodes'], ep_id)


def test_processed_episode_still_findable_by_transcript():
    slug = _feed('processed-transcript')
    ep_id = _eid()
    db.bulk_upsert_discovered_episodes(slug, [_episode(ep_id, title='Zylophraxis Show')])
    db.upsert_episode(slug, ep_id, status='processed')
    db.save_episode_details(slug, ep_id, transcript_text='mentions targaryen dragons extensively')
    db.index_episode(ep_id, slug)
    assert _episode_hit(db.search_grouped('targaryen')['transcripts'], ep_id)


def test_upsert_episode_new_row_is_indexed_immediately():
    slug = _feed('upsert-new-row')
    ep_id = _eid()
    db.upsert_episode(slug, ep_id, title='Vexillological Chronicles', description='flags and heraldry')
    assert _episode_hit(db.search_grouped('Vexillological')['episodes'], ep_id)


def test_reindex_scopes_by_podcast_slug_not_bare_episode_id():
    """Two podcasts can share an episode_id GUID; reindexing one must not lose the other's
    row, so index_episodes scopes its DELETE/SELECT by (episode_id, podcast_slug) pairs."""
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


def test_rebuild_streams_source_rows_in_bounded_write_chunks(monkeypatch):
    slug = _feed('rebuild-lock-order')
    db.upsert_episode(slug, _eid(), original_url='https://example.com/a.mp3',
                      title='Indexed episode', status='processed')
    conn = db.get_connection()
    order = []
    batch_sizes = []
    real_execute = conn.execute
    real_executemany = conn.executemany

    def spy_execute(sql, *a):
        order.append(('execute', ' '.join(str(sql).split())[:120]))
        return real_execute(sql, *a)

    def spy_executemany(sql, *a):
        order.append(('executemany', ' '.join(str(sql).split())[:120]))
        batch_sizes.append(len(a[0]))
        return real_executemany(sql, *a)

    monkeypatch.setattr(conn, 'execute', spy_execute)
    monkeypatch.setattr(conn, 'executemany', spy_executemany)

    assert db.rebuild_search_index() > 0

    kinds = [k for k, _ in order]
    sqls = [q for _, q in order]
    first_write = next(i for i, q in enumerate(sqls) if q.startswith('INSERT INTO search_index_new'))
    first_source_read = next(i for i, q in enumerate(sqls) if q.startswith('SELECT slug'))
    assert first_source_read < first_write
    assert batch_sizes and max(batch_sizes) <= 50
    assert kinds[first_write] == 'executemany'
    swap = next(i for i, sql in enumerate(sqls) if sql.startswith('DROP TABLE search_index'))
    assert sqls[swap + 1].startswith('ALTER TABLE')
    assert sqls[swap + 1].endswith('RENAME TO search_index')
    assert sqls[swap + 2] == 'SELECT COUNT(*) FROM search_index'


def test_rebuild_still_indexes_every_content_type(monkeypatch):
    slug = _feed('rebuild-content-types')
    db.upsert_episode(slug, _eid(), original_url='https://example.com/b.mp3',
                      title='Findable episode', status='processed')

    assert db.rebuild_search_index() > 0

    conn = db.get_connection()
    types = {r['content_type'] for r in conn.execute(
        'SELECT DISTINCT content_type FROM search_index').fetchall()}
    assert 'episode' in types and 'podcast' in types


def test_rebuild_swaps_a_shadow_table_and_keeps_the_fts_definition():
    conn = db.get_connection()
    before = conn.execute(
        "SELECT sql FROM sqlite_master WHERE name = 'search_index'").fetchone()['sql']
    ep = _eid()
    slug = _feed('swap-podcast')
    db.upsert_episode(slug, ep, original_url='https://example.com/a.mp3',
                      title='Rebuild swap marker title', status='processed')
    count = db.rebuild_search_index()
    assert count >= 1
    names = {r['name'] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE name LIKE 'search_index%' AND type = 'table'")}
    assert 'search_index_new' not in names
    after = conn.execute(
        "SELECT sql FROM sqlite_master WHERE name = 'search_index'").fetchone()['sql']
    assert after.replace('"', '') == before.replace('"', '')
    assert any(r['content_id'] == ep for r in conn.execute(
        "SELECT content_id FROM search_index WHERE search_index MATCH 'swap'"))


def test_rows_indexed_during_the_fill_survive_the_swap(monkeypatch):
    slug = _feed('late-write-podcast')
    conn = db.get_connection()
    late_ep = _eid()
    real_execute = conn.execute
    begin_count = 0

    def write_during_fill(sql, *a):
        nonlocal begin_count
        if str(sql).startswith('BEGIN IMMEDIATE'):
            begin_count += 1
        if begin_count == 2:
            begin_count += 1
            other = sqlite3.connect(str(db.db_path))
            other.execute(
                "INSERT INTO episodes (podcast_id, episode_id, original_url, title, status) "
                "SELECT id, ?, 'https://example.com/late.mp3', 'Late arrival title', "
                "'discovered' FROM podcasts WHERE slug = ?", (late_ep, slug))
            other.commit()
            other.close()
        return real_execute(sql, *a)

    monkeypatch.setattr(conn, 'execute', write_during_fill)
    db.rebuild_search_index()
    assert begin_count >= 2
    assert any(r['content_id'] == late_ep for r in conn.execute(
        "SELECT content_id FROM search_index WHERE search_index MATCH 'arrival'"))


@pytest.mark.parametrize('operation', ['update', 'delete', 'rowid_reuse'])
def test_source_changes_during_fill_are_replayed(monkeypatch, operation):
    slug = _feed(f'journal-{operation}')
    episode_id = _eid()
    db.upsert_episode(
        slug, episode_id, original_url='https://example.com/old.mp3',
        title='Obsolete journal title', status='discovered')
    conn = db.get_connection()
    row_id = conn.execute(
        "SELECT e.id FROM episodes e JOIN podcasts p ON p.id = e.podcast_id "
        "WHERE p.slug = ? AND e.episode_id = ?", (slug, episode_id)).fetchone()[0]
    real_execute = conn.execute
    begin_count = 0

    def change_during_fill(sql, *args):
        nonlocal begin_count
        if str(sql).startswith('BEGIN IMMEDIATE'):
            begin_count += 1
        if begin_count == 2:
            begin_count += 1
            other = sqlite3.connect(str(db.db_path))
            if operation == 'update':
                other.execute(
                    "UPDATE episodes SET title = 'Current journal title' WHERE id = ?",
                    (row_id,))
            else:
                other.execute("DELETE FROM episodes WHERE id = ?", (row_id,))
                if operation == 'rowid_reuse':
                    other.execute(
                        "INSERT INTO episodes "
                        "(id, podcast_id, episode_id, original_url, title, status) "
                        "SELECT ?, id, ?, 'https://example.com/new.mp3', "
                        "'Replacement journal title', 'discovered' "
                        "FROM podcasts WHERE slug = ?",
                        (row_id, episode_id, slug))
            other.commit()
            other.close()
        return real_execute(sql, *args)

    monkeypatch.setattr(conn, 'execute', change_during_fill)
    db.rebuild_search_index()
    rows = conn.execute(
        "SELECT title FROM search_index WHERE content_type = 'episode' "
        "AND content_id = ? AND podcast_slug = ?", (episode_id, slug)).fetchall()
    if operation == 'delete':
        assert rows == []
    else:
        expected = 'Current journal title' if operation == 'update' else 'Replacement journal title'
        assert [row['title'] for row in rows] == [expected]


def test_sponsor_and_pattern_changes_during_fill_are_replayed(monkeypatch):
    sponsor_id = db.create_known_sponsor('Obsolete Sponsor Name')
    pattern_id = db.create_ad_pattern(
        'global', text_template='sponsor message', sponsor_id=sponsor_id)
    conn = db.get_connection()
    real_execute = conn.execute
    begin_count = 0

    def change_during_fill(sql, *args):
        nonlocal begin_count
        if str(sql).startswith('BEGIN IMMEDIATE'):
            begin_count += 1
        if begin_count == 2:
            begin_count += 1
            other = sqlite3.connect(str(db.db_path))
            other.execute(
                "UPDATE known_sponsors SET name = 'Current Sponsor Name' WHERE id = ?",
                (sponsor_id,))
            other.commit()
            other.close()
        return real_execute(sql, *args)

    monkeypatch.setattr(conn, 'execute', change_during_fill)
    db.rebuild_search_index()
    sponsor = conn.execute(
        "SELECT title FROM search_index WHERE content_type = 'sponsor' AND content_id = ?",
        (str(sponsor_id),)).fetchone()
    pattern = conn.execute(
        "SELECT title FROM search_index WHERE content_type = 'pattern' AND content_id = ?",
        (str(pattern_id),)).fetchone()
    assert sponsor['title'] == 'Current Sponsor Name'
    assert pattern['title'] == 'Current Sponsor Name'


def test_failed_fill_drops_the_shadow_and_keeps_the_old_index(monkeypatch):
    conn = db.get_connection()
    before = conn.execute("SELECT COUNT(*) FROM search_index").fetchone()[0]

    def boom(sql, *a):
        raise sqlite3.OperationalError('disk I/O error')

    monkeypatch.setattr(conn, 'executemany', boom)
    with pytest.raises(sqlite3.OperationalError):
        db.rebuild_search_index()
    assert not conn.in_transaction
    names = [r['name'] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE 'search_index_new%'")]
    assert names == []
    assert conn.execute("SELECT COUNT(*) FROM search_index").fetchone()[0] == before


def test_stale_shadow_from_a_crash_is_dropped_on_the_next_rebuild():
    conn = db.get_connection()
    conn.execute(
        "CREATE VIRTUAL TABLE search_index_new_4194304_1 USING fts5(content_type, content_id, podcast_slug, title, body, metadata)")
    conn.commit()
    db.rebuild_search_index()
    names = [r['name'] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE 'search_index_new%'")]
    assert names == []


def test_rebuild_drops_stale_shadow_named_for_a_live_pid():
    conn = db.get_connection()
    name = f'search_index_new_{os.getpid()}_{threading.get_ident()}'
    conn.execute(
        f"CREATE VIRTUAL TABLE {name} USING fts5(content_type, content_id, "
        "podcast_slug, title, body, metadata)")
    conn.commit()

    db.rebuild_search_index()
    names = [row['name'] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE 'search_index_new%'")]
    assert names == []
