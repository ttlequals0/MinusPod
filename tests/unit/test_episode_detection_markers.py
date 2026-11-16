"""episode_detection_markers: lazy cache behind get_detection_rows, result unchanged."""
import json
import sqlite3

import pytest

from database import Database

MARKERS = [{'start': 10.0, 'end': 40.0, 'confidence': 0.9}]


def _direct(db):
    """The pre-cache query, verbatim, as the reference for the unchanged result."""
    return sorted((dict(r) for r in db.get_connection().execute('''
        SELECT e.podcast_id AS podcast_id,
               p.slug AS feed_slug, p.title AS feed_title,
               p.segment_category_actions AS segment_category_actions,
               e.episode_id, e.title AS episode_title,
               e.published_at, e.created_at, e.original_file,
               e.processed_version, e.original_duration,
               ed.ad_markers_json
        FROM episodes e
        JOIN podcasts p ON e.podcast_id = p.id
        JOIN episode_details ed ON ed.episode_id = e.id
        WHERE ed.ad_markers_json IS NOT NULL
          AND ed.ad_markers_json NOT IN ('', '[]', 'null')
    ''')), key=lambda r: r['episode_id'])


def _rows(db):
    return sorted(db.get_detection_rows(), key=lambda r: r['episode_id'])


def _cache(db):
    return {r['episode_id']: r['ad_markers_json'] for r in db.get_connection().execute(
        'SELECT episode_id, ad_markers_json FROM episode_detection_markers')}


def _pk(db, ep):
    return db.get_connection().execute(
        'SELECT id FROM episodes WHERE episode_id = ?', (ep,)).fetchone()['id']


@pytest.fixture
def seeded(temp_db):
    db = temp_db
    db.create_podcast('show-a', 'http://example.com/a.xml', 'Show A')
    for ep, markers in (('ep1', MARKERS), ('ep2', [{'start': 1.0, 'end': 2.0}]), ('ep3', [])):
        db.upsert_episode('show-a', ep, title='T', original_url='http://example.com/a.mp3')
        db.save_episode_details('show-a', ep, ad_markers=markers)
    db.upsert_episode('show-a', 'ep4', title='T', original_url='http://example.com/a.mp3')
    return db


def test_same_rows_before_and_after_the_cache_is_filled(seeded):
    expected = _direct(seeded)
    assert len(expected) == 2
    assert _cache(seeded) == {}
    assert _rows(seeded) == expected
    assert _cache(seeded) != {}
    assert _rows(seeded) == expected


def test_cached_call_serves_from_the_cache_table(seeded):
    _rows(seeded)
    pk = _pk(seeded, 'ep1')
    conn = seeded.get_connection()
    conn.execute('UPDATE episode_detection_markers SET ad_markers_json = ? WHERE episode_id = ?',
                 ('[{"start": 99.0}]', pk))
    conn.commit()
    got = {r['episode_id']: r['ad_markers_json'] for r in seeded.get_detection_rows()}
    assert got['ep1'] == '[{"start": 99.0}]'


def test_cached_call_issues_no_read_of_episode_details(seeded):
    _rows(seeded)
    conn = seeded.get_connection()
    seen = []
    conn.set_trace_callback(seen.append)
    try:
        seeded.get_detection_rows()
    finally:
        conn.set_trace_callback(None)
    assert not [s for s in seen if s.startswith('SELECT episode_id, ad_markers_json FROM episode_details')]


def test_marker_update_drops_the_cache_row_and_returns_new_markers(seeded):
    _rows(seeded)
    pk = _pk(seeded, 'ep1')
    assert pk in _cache(seeded)
    new = [{'start': 50.0, 'end': 60.0}]
    seeded.save_episode_details('show-a', 'ep1', ad_markers=new)
    assert pk not in _cache(seeded)
    got = {r['episode_id']: json.loads(r['ad_markers_json']) for r in seeded.get_detection_rows()}
    assert got['ep1'] == new
    assert _rows(seeded) == _direct(seeded)


def test_empty_markers_get_a_null_cache_row_and_are_not_returned(seeded):
    rows = seeded.get_detection_rows()
    assert {r['episode_id'] for r in rows} == {'ep1', 'ep2'}
    assert _cache(seeded)[_pk(seeded, 'ep3')] is None
    assert _pk(seeded, 'ep4') not in _cache(seeded)
    assert {r['episode_id'] for r in seeded.get_detection_rows()} == {'ep1', 'ep2'}


def test_row_keys_match_the_legacy_query(seeded):
    assert set(seeded.get_detection_rows()[0]) == set(_direct(seeded)[0])


def test_details_delete_and_episode_cascade_remove_cache_rows(seeded):
    _rows(seeded)
    conn = seeded.get_connection()
    p1, p2 = _pk(seeded, 'ep1'), _pk(seeded, 'ep2')
    conn.execute('DELETE FROM episode_details WHERE episode_id = ?', (p1,))
    conn.execute('DELETE FROM episodes WHERE id = ?', (p2,))
    conn.commit()
    assert p1 not in _cache(seeded) and p2 not in _cache(seeded)
    assert _rows(seeded) == _direct(seeded) == []


def test_marker_write_between_read_and_store_is_not_cached_stale(seeded):
    real = seeded._store_detection_markers

    def store(computed):
        seeded.save_episode_details('show-a', 'ep2', ad_markers=[{'start': 5.0, 'end': 6.0}])
        real(computed)

    seeded._store_detection_markers = store
    try:
        seeded.get_detection_rows()
    finally:
        del seeded._store_detection_markers
    assert _pk(seeded, 'ep2') not in _cache(seeded)
    assert _pk(seeded, 'ep1') in _cache(seeded)
    assert _rows(seeded) == _direct(seeded)


def test_cache_write_inside_a_transaction_is_skipped_but_rows_are_correct(seeded):
    conn = seeded.get_connection()
    conn.execute('BEGIN')
    try:
        assert _rows(seeded) == _direct(seeded)
    finally:
        conn.rollback()
    assert _cache(seeded) == {}


def test_cache_write_failure_still_returns_correct_rows(seeded, monkeypatch):
    def locked(*args, **kwargs):
        raise sqlite3.OperationalError('database is locked')

    monkeypatch.setattr(seeded, 'transaction', locked)
    assert _rows(seeded) == _direct(seeded)
    assert _cache(seeded) == {}


class TestMigration:
    def _objects(self, db):
        return {r[0] for r in db.get_connection().execute(
            "SELECT name FROM sqlite_master WHERE name LIKE '%detection_markers%'")}

    def test_fresh_database_has_table_and_triggers(self, temp_db):
        assert len(self._objects(temp_db)) == 4

    def test_existing_database_gets_them_back(self, seeded, temp_dir):
        conn = seeded.get_connection()
        for name in [n for n in self._objects(seeded) if n.endswith(('_insert', '_update', '_delete'))]:
            conn.execute(f'DROP TRIGGER {name}')
        conn.execute('DROP TABLE episode_detection_markers')
        conn.commit()
        assert self._objects(seeded) == set()
        reopened = object.__new__(Database)
        reopened._initialized = False
        Database.__init__(reopened, data_dir=temp_dir)
        assert len(self._objects(reopened)) == 4
        assert _rows(reopened) == _direct(reopened)
