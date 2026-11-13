"""episode_snap_stats: lazy cache behind the addressing avgSnapDistance, result unchanged."""
import json

import pytest

import database.stats as stats_mod
from database import Database

RECENT = '2999-01-01T00:00:00Z'


def _marker(start, llm_start, mode):
    return {'start': start, 'end': start + 10, 'llm_start': llm_start, 'llm_end': llm_start + 10,
            'addressing_mode': mode}


def _legacy_avg(db, slug=None):
    """The pre-cache statement, verbatim, as the reference for the unchanged result."""
    conn = db.get_connection()
    sums = {'timestamps': [0.0, 0], 'segment_ids': [0.0, 0]}
    for r in conn.execute(
        """SELECT ed.ad_markers_json FROM episode_details ed
           JOIN episodes e ON e.id = ed.episode_id
           JOIN podcasts p ON p.id = e.podcast_id
           WHERE ed.ad_markers_json LIKE '%"llm_start"%'
             AND COALESCE(e.processed_at, e.updated_at) >= '2000-01-01T00:00:00Z'
             AND (? IS NULL OR p.slug = ?)
           ORDER BY COALESCE(e.processed_at, e.updated_at) DESC LIMIT 2000""", [slug, slug],
    ).fetchall():
        for m in json.loads(r['ad_markers_json']):
            found = stats_mod._marker_snap_dist(m)
            if found:
                sums[found[0]][0] += found[1]
                sums[found[0]][1] += 1
    return {mode: round(t / n, 3) if n else None for mode, (t, n) in sums.items()}


def _avg(db, slug=None):
    by_mode = db.get_addressing_stats(slug)['byMode']
    return {mode: by_mode[mode]['avgSnapDistance'] for mode in by_mode}


def _cache(db):
    return {r['episode_id']: tuple(r)[1:] for r in db.get_connection().execute(
        'SELECT * FROM episode_snap_stats')}


def _pk(db, slug, ep):
    return db.get_episode(slug, ep)['id']


def _stamp(db, ep, value=RECENT):
    conn = db.get_connection()
    conn.execute('UPDATE episodes SET processed_at = ? WHERE episode_id = ?', (value, ep))
    conn.commit()


@pytest.fixture
def seeded(temp_db):
    db = temp_db
    for slug in ('show-a', 'show-b'):
        db.create_podcast(slug, f'http://example.com/{slug}.xml', slug)
    plan = [
        ('show-a', 'ep1', [_marker(12.0, 10.0, 'timestamps'), _marker(30.0, 20.0, 'segment_ids'),
                           {'start': 5.0, 'end': 6.0}]),
        ('show-a', 'ep2', [_marker(9.0, 5.0, 'timestamps')]),
        ('show-b', 'ep3', [_marker(40.0, 33.0, 'segment_ids')]),
        ('show-b', 'ep4', [{'start': 1.0, 'end': 2.0}]),
    ]
    for slug, ep, markers in plan:
        db.upsert_episode(slug, ep, title='T', original_url='http://example.com/a.mp3')
        db.save_episode_details(slug, ep, ad_markers=markers)
        _stamp(db, ep)
    db.upsert_episode('show-a', 'ep5', title='T', original_url='http://example.com/a.mp3')
    return db


@pytest.mark.parametrize('slug', [None, 'show-a', 'show-b', 'missing-show'])
def test_result_matches_the_legacy_statement(seeded, slug):
    expected = _legacy_avg(seeded, slug)
    assert _avg(seeded, slug) == expected
    assert _avg(seeded, slug) == expected


def test_cache_filled_then_served_without_reading_details(seeded):
    first = _avg(seeded)
    pk = _pk(seeded, 'show-a', 'ep1')
    assert _cache(seeded)[pk] == (1, 4.0, 1, 20.0, 1)
    assert _pk(seeded, 'show-b', 'ep4') in _cache(seeded)
    assert _cache(seeded)[_pk(seeded, 'show-b', 'ep4')][0] == 0
    conn = seeded.get_connection()
    conn.execute('UPDATE episode_snap_stats SET ts_total = ts_total + 100, ts_n = ts_n + 1 '
                 'WHERE episode_id = ?', (pk,))
    conn.commit()
    assert _avg(seeded) != first


def test_marker_update_invalidates_the_cache_row(seeded):
    _avg(seeded)
    pk = _pk(seeded, 'show-a', 'ep2')
    assert pk in _cache(seeded)
    seeded.save_episode_details('show-a', 'ep2', ad_markers=[_marker(50.0, 10.0, 'timestamps')])
    assert pk not in _cache(seeded)
    assert _avg(seeded) == _legacy_avg(seeded)


def test_episodes_outside_the_window_are_excluded(seeded):
    _stamp(seeded, 'ep2', '2000-01-01T00:00:00Z')
    assert _avg(seeded)['timestamps'] == 4.0


def test_episode_cap_keeps_the_newest(seeded, monkeypatch):
    monkeypatch.setattr(stats_mod, '_SNAP_MAX_EPISODES', 1)
    _stamp(seeded, 'ep3', '3000-01-01T00:00:00Z')
    assert _avg(seeded) == {'timestamps': None, 'segment_ids': 14.0}


def test_unparseable_markers_count_toward_the_cap_like_before(seeded):
    conn = seeded.get_connection()
    conn.execute("UPDATE episode_details SET ad_markers_json = '\"llm_start\" not json' "
                 "WHERE episode_id = ?", (_pk(seeded, 'show-a', 'ep2'),))
    conn.commit()
    assert _avg(seeded)['timestamps'] == 4.0
    assert _cache(seeded)[_pk(seeded, 'show-a', 'ep2')] == (1, 0.0, 0, 0.0, 0)


class TestMigration:
    def _objects(self, db):
        return {r[0] for r in db.get_connection().execute(
            "SELECT name FROM sqlite_master WHERE name LIKE '%snap_stats%' "
            "OR name = 'idx_episodes_activity_at'")}

    def test_fresh_database_has_table_triggers_and_index(self, temp_db):
        assert len(self._objects(temp_db)) == 5

    def test_existing_database_gets_them_back(self, seeded, temp_dir):
        conn = seeded.get_connection()
        for name in [n for n in self._objects(seeded) if n.startswith('episode_snap_stats_')]:
            conn.execute(f'DROP TRIGGER {name}')
        conn.execute('DROP TABLE episode_snap_stats')
        conn.execute('DROP INDEX idx_episodes_activity_at')
        conn.commit()
        assert self._objects(seeded) == set()
        reopened = object.__new__(Database)
        reopened._initialized = False
        Database.__init__(reopened, data_dir=temp_dir)
        assert len(self._objects(reopened)) == 5
        assert _avg(reopened) == _legacy_avg(reopened)

    def test_window_query_uses_the_expression_index(self, temp_db):
        plan = ' '.join(r[3] for r in temp_db.get_connection().execute(
            "EXPLAIN QUERY PLAN SELECT id FROM episodes "
            "WHERE COALESCE(processed_at, updated_at) >= 'x'"))
        assert 'idx_episodes_activity_at' in plan
