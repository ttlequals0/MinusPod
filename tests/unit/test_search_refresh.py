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
