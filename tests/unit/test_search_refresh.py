from tests.app_bootstrap import bootstrap

bootstrap('search_refresh_')

import pytest

import database

db = database.Database()
SLUG = EP = None
_counter = 0


def _setup():
    global SLUG, EP, _counter
    _counter += 1
    SLUG = f'refresh-show-{_counter}'
    EP = f'c1d2e3f4a5{_counter:02d}'
    db.create_podcast(SLUG, f'https://example.com/refresh{_counter}.xml', 'Example Show')
    db.upsert_episode(SLUG, EP, title='Original Episode', description='<p>desc</p>')
    db.rebuild_search_index()


def _journal_count():
    return db.get_connection().execute("SELECT COUNT(*) FROM search_index_changes").fetchone()[0]


def test_refresh_applies_journal_and_clears_it():
    _setup()
    conn = db.get_connection()
    conn.execute("UPDATE episodes SET title = 'Renamed Episode' WHERE episode_id = ?", (EP,))
    conn.commit()
    assert db.refresh_search_index() == 1
    assert _journal_count() == 0
    assert db.search_grouped('Renamed')['episodes']
    assert all(e['episodeId'] != EP for e in db.search_grouped('Original')['episodes'])


def test_refresh_is_noop_when_journal_empty():
    _setup()
    assert db.refresh_search_index() == 0


def test_refresh_removes_deleted_rows():
    _setup()
    conn = db.get_connection()
    conn.execute("DELETE FROM episodes WHERE episode_id = ?", (EP,))
    conn.commit()
    assert db.refresh_search_index() >= 1
    assert all(e['episodeId'] != EP for e in db.search_grouped('Original')['episodes'])


def test_refresh_chunks_and_yields(monkeypatch):
    _setup()
    monkeypatch.setattr('database.search._REBUILD_TX_ROWS', 1)
    sleeps = []
    monkeypatch.setattr('database.search.time.sleep', lambda s: sleeps.append(s))
    conn = db.get_connection()
    conn.execute("UPDATE episodes SET title = 'Chunked One' WHERE episode_id = ?", (EP,))
    conn.execute("UPDATE podcasts SET title = 'Chunked Show' WHERE slug = ?", (SLUG,))
    conn.commit()
    assert db.refresh_search_index() == 2
    assert len(sleeps) == 1 and all(s == 0.02 for s in sleeps)
    assert _journal_count() == 0


def test_rebuild_yields_between_write_batches(monkeypatch):
    _setup()
    sleeps = []
    monkeypatch.setattr('database.search.time.sleep', lambda s: sleeps.append(s))
    db.rebuild_search_index()
    assert sleeps and all(s == 0.02 for s in sleeps)


def test_change_journaled_during_refresh_survives_for_next_refresh(monkeypatch):
    _setup()
    other = f'{EP[:-2]}zz'
    db.upsert_episode(SLUG, other, title='Second Episode')
    db.rebuild_search_index()
    conn = db.get_connection()
    conn.execute("UPDATE episodes SET title = 'First Changed' WHERE episode_id = ?", (EP,))
    conn.commit()
    real = database.search.SearchMixin._current_search_row
    calls = []

    def journaling(tx, *args):
        if not calls:
            calls.append(1)
            tx.execute("UPDATE episodes SET title = 'Second Changed' WHERE episode_id = ?", (other,))
        return real(tx, *args)

    monkeypatch.setattr(database.search.SearchMixin, '_current_search_row', staticmethod(journaling))
    assert db.refresh_search_index() == 1
    remaining = db.get_connection().execute(
        "SELECT DISTINCT content_id FROM search_index_changes").fetchall()
    assert [r[0] for r in remaining] == [other]
    monkeypatch.undo()
    assert db.refresh_search_index() == 1
    assert db.search_grouped('Second Changed')['episodes']
    assert _journal_count() == 0


def test_refresh_raises_while_another_holder_owns_the_lock():
    _setup()
    with database.search._search_rebuild_lock(db.db_path):
        with pytest.raises(RuntimeError, match='already in progress'):
            db.refresh_search_index()


def test_drop_stale_shadows_uses_chunked_purge(monkeypatch):
    conn = db.get_connection()
    name = 'search_index_new_1_1'
    conn.execute(f'CREATE VIRTUAL TABLE "{name}" USING fts5(a)')
    conn.commit()
    purged = []
    monkeypatch.setattr(db, '_purge_retired_shadow', lambda n: purged.append(n))
    db._drop_stale_shadows(conn)
    assert name in purged
    conn.execute(f'DROP TABLE IF EXISTS "{name}"')
    conn.commit()


def test_drop_stale_shadows_removes_the_table_and_survives_a_purge_failure(monkeypatch):
    conn = db.get_connection()
    good, bad = 'search_index_new_2_1', 'search_index_new_2_2'
    for name in (good, bad):
        conn.execute(f'CREATE VIRTUAL TABLE "{name}" USING fts5(a)')
    conn.commit()
    real = db._purge_retired_shadow

    def flaky(name):
        if name == bad:
            raise RuntimeError('database is locked')
        real(name)

    monkeypatch.setattr(db, '_purge_retired_shadow', flaky)
    db._drop_stale_shadows(conn)
    left = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE name LIKE 'search_index_new_2_%'")}
    conn.execute(f'DROP TABLE IF EXISTS "{bad}"')
    conn.commit()
    assert good not in left
    assert bad in left


def test_routine_stale_shadow_drop_logs_info_not_warning(caplog):
    conn = db.get_connection()
    name = 'search_index_new_3_1'
    conn.execute(f'CREATE VIRTUAL TABLE "{name}" USING fts5(a)')
    conn.commit()
    with caplog.at_level('INFO', logger='database.search'):
        db._drop_stale_shadows(conn)
    records = [r for r in caplog.records if 'stale search index shadow' in r.getMessage()]
    assert [r.levelname for r in records] == ['INFO']
