"""episode_marker_refs: extraction, writer upkeep, backfill step, lookups and schema on fresh and migrated databases."""
import json

import pytest

import database.episodes as episodes_mod
from database import Database
from database.marker_refs import replace_marker_refs
from database.schema import deferred
from utils.markers import extract_marker_refs, normalize_sponsor_key

SLUG = 'example-podcast'
GATE = deferred.MARKER_REFS_GATE
PAGE = 'Episode'


class Killed(BaseException):
    """A worker killed between two pages; nothing in the step may catch it."""


def _refs(db, episode=None):
    sql = "SELECT e.episode_id AS eid, r.ref_type, r.ref_value FROM episode_marker_refs r JOIN episodes e ON e.id = r.episode_pk"
    rows = db.get_connection().execute(sql).fetchall()
    return {(r['ref_type'], r['ref_value']) for r in rows if episode in (None, r['eid'])}


def _episode(db, n, markers=None, processed_at=None, retained=True):
    if db.get_podcast_by_slug(SLUG) is None:
        db.create_podcast(SLUG, 'https://example.com/feed.xml', SLUG)
    eid = f'{n:012x}'
    db.upsert_episode(slug=SLUG, episode_id=eid, original_url='https://example.com/a.mp3', title=PAGE,
                      original_duration=300.0, status='processed')
    conn = db.get_connection()
    conn.execute("UPDATE episodes SET processed_at = ?, original_file = ? WHERE episode_id = ?",
                 (processed_at or f'2026-01-01T00:{n % 60:02d}:00Z', 'x.mp3' if retained else None, eid))
    conn.commit()
    if markers is not None:
        db.save_episode_details(SLUG, eid, ad_markers=markers)
    return eid


def _raw_markers(db, eid, markers):
    """Write markers behind the refs' back, as an existing database holds them before the upgrade."""
    conn = db.get_connection()
    pk = conn.execute("SELECT id FROM episodes WHERE episode_id = ?", (eid,)).fetchone()['id']
    conn.execute("INSERT INTO episode_details (episode_id, ad_markers_json, original_segments_json) VALUES (?, ?, '[]') "
                 "ON CONFLICT(episode_id) DO UPDATE SET ad_markers_json = excluded.ad_markers_json",
                 (pk, json.dumps(markers) if not isinstance(markers, str) else markers))
    conn.commit()


def _pending(db):
    conn = db.get_connection()
    conn.execute("DELETE FROM schema_migrations WHERE name = ?", (GATE,))
    conn.execute("DELETE FROM episode_marker_refs")
    conn.commit()
    db._init_schema()


def test_extraction_reads_top_level_merged_members_and_nested_values():
    markers = [
        {'start': 1, 'end': 2, 'pattern_id': 7, 'sponsor': ' Acme '},
        {'start': 3, 'end': 4, 'sponsor': 'ACME', 'merged_member_spans': [
            {'pattern_id': 9, 'sponsor': 'Globex'}, {'pattern_id': 7}, 'junk', None]},
        {'start': 5, 'end': 6, 'pattern_id': None, 'sponsor': '  '},
        {'start': 7, 'end': 8, 'pattern_id': True, 'sponsor': 12, 'extra': {'pattern_id': 11}},
    ]
    assert extract_marker_refs(markers) == {
        ('pattern', '7'), ('pattern', '9'), ('pattern', '11'), ('sponsor', 'acme'), ('sponsor', 'globex')}


@pytest.mark.parametrize('value', [None, [], {}, 'text', 5, [None, 3, 'x'], [{'start': 1}]])
def test_extraction_of_no_markers_or_unusable_values_is_empty(value):
    assert extract_marker_refs(value) == set()


def test_writer_fills_replaces_and_clears_refs(temp_db):
    eid = _episode(temp_db, 1, [{'pattern_id': 7, 'sponsor': 'Acme'}])
    assert _refs(temp_db) == {('pattern', '7'), ('sponsor', 'acme')}
    temp_db.save_episode_details(SLUG, eid, ad_markers=[{'pattern_id': 8}])
    assert _refs(temp_db) == {('pattern', '8')}
    temp_db.save_episode_details(SLUG, eid, transcript_text='unrelated write')
    assert _refs(temp_db) == {('pattern', '8')}
    temp_db.save_episode_details(SLUG, eid, ad_markers=[])
    assert _refs(temp_db) == set()


def test_a_failed_marker_write_leaves_markers_and_refs_unchanged(temp_db, monkeypatch):
    eid = _episode(temp_db, 1, [{'pattern_id': 7}])

    def boom(*args):
        raise RuntimeError('refs failed')
    monkeypatch.setattr(episodes_mod, 'replace_marker_refs', boom)
    with pytest.raises(RuntimeError):
        temp_db.save_episode_details(SLUG, eid, ad_markers=[{'pattern_id': 8}])
    stored = temp_db.get_connection().execute(
        "SELECT ad_markers_json FROM episode_details d JOIN episodes e ON e.id = d.episode_id WHERE e.episode_id = ?",
        (eid,)).fetchone()[0]
    assert json.loads(stored) == [{'pattern_id': 7}]
    assert _refs(temp_db) == {('pattern', '7')}


def test_refs_follow_clears_and_deletes(temp_db):
    kept = _episode(temp_db, 1, [{'pattern_id': 7}])
    cleared = _episode(temp_db, 2, [{'pattern_id': 7}])
    ad_cleared = _episode(temp_db, 3, [{'pattern_id': 7}])
    batch_cleared = _episode(temp_db, 4, [{'pattern_id': 8}])
    batch_ad_cleared = _episode(temp_db, 5, [{'pattern_id': 9}])
    deleted = _episode(temp_db, 6, [{'pattern_id': 10}])
    temp_db.clear_episode_details(SLUG, cleared)
    temp_db.clear_episode_ad_data(SLUG, ad_cleared)
    temp_db.get_connection().commit()
    temp_db.batch_clear_episode_details(SLUG, [batch_cleared])
    temp_db.batch_clear_episode_ad_data(SLUG, [batch_ad_cleared])
    conn = temp_db.get_connection()
    conn.execute("DELETE FROM episodes WHERE episode_id = ?", (deleted,))
    conn.commit()
    assert _refs(temp_db, kept) == {('pattern', '7')}
    assert _refs(temp_db) == {('pattern', '7')}


def test_backfill_fills_an_existing_database_and_is_idempotent(temp_db):
    eids = [_episode(temp_db, n) for n in range(1, 8)]
    for n, eid in enumerate(eids):
        _raw_markers(temp_db, eid, [{'pattern_id': 7 + n % 2, 'sponsor': f'Sponsor {n % 3}'}])
    _episode(temp_db, 20)
    bad = _episode(temp_db, 21)
    _raw_markers(temp_db, bad, '{not json')
    _pending(temp_db)
    assert temp_db.marker_refs_pending() and _refs(temp_db) == set()
    assert temp_db.run_deferred_upgrade_steps()
    first = _refs(temp_db)
    assert not temp_db.marker_refs_pending()
    assert first == {('pattern', '7'), ('pattern', '8'), ('sponsor', 'sponsor 0'), ('sponsor', 'sponsor 1'),
                     ('sponsor', 'sponsor 2')}
    _pending(temp_db)
    assert temp_db.run_deferred_upgrade_steps()
    assert _refs(temp_db) == first


def test_backfill_pages_and_resumes_after_an_interrupted_run(temp_db, monkeypatch):
    monkeypatch.setattr(deferred, 'MARKER_REFS_PAGE_EPISODES', 2)
    for n in range(1, 8):
        _raw_markers(temp_db, _episode(temp_db, n), [{'pattern_id': 100 + n}])
    _pending(temp_db)
    seen = []

    def pause():
        seen.append(1)
        if len(seen) == 2:
            raise Killed
    with pytest.raises(Killed):
        temp_db.run_deferred_upgrade_steps(clock=lambda: 5.0, pause=pause)
    temp_db.get_connection().rollback()
    partial = len(_refs(temp_db))
    assert 0 < partial < 7 and temp_db.marker_refs_pending()
    row = temp_db.get_connection().execute(
        "SELECT cursor FROM deferred_upgrade_steps WHERE name = ?", (GATE,)).fetchone()
    assert json.loads(row['cursor']) == {'after': 4}
    assert temp_db.run_deferred_upgrade_steps(clock=lambda: 5.0 + deferred.STALE_HEARTBEAT_SECONDS + 1)
    assert _refs(temp_db) == {('pattern', str(100 + n)) for n in range(1, 8)}
    assert not temp_db.marker_refs_pending()


def test_a_fresh_database_has_nothing_to_backfill(temp_db):
    assert not temp_db.marker_refs_pending()
    assert GATE not in temp_db.pending_upgrade_steps()


def test_a_marker_written_while_the_backfill_is_pending_survives_it(temp_db):
    eid = _episode(temp_db, 1)
    _raw_markers(temp_db, eid, [{'pattern_id': 5}])
    _pending(temp_db)
    temp_db.save_episode_details(SLUG, eid, ad_markers=[{'pattern_id': 6}])
    assert temp_db.run_deferred_upgrade_steps()
    assert _refs(temp_db) == {('pattern', '6')}


def _plan(db, call):
    conn = db.get_connection()
    statements = []
    conn.set_trace_callback(lambda sql: statements.append(sql) if 'SELECT p.slug' in sql else None)
    call()
    conn.set_trace_callback(None)
    return ' '.join(r[3] for r in conn.execute('EXPLAIN QUERY PLAN ' + statements[-1]))


@pytest.mark.parametrize('scope', [{'scope': 'global'}, {'scope': 'podcast', 'podcast_id': SLUG},
                                   {'scope': 'network', 'network_id': 'example-net'}])
def test_marker_and_sponsor_lookups_use_the_refs_index_and_never_scan_episode_details(temp_db, scope):
    pattern = {'id': 7, **scope}
    for plan in (_plan(temp_db, lambda: temp_db.get_pattern_marker_episodes(pattern, 5)),
                 _plan(temp_db, lambda: temp_db.get_pattern_sponsor_episodes(pattern, ['Acme', 'Globex'], 5))):
        assert 'SEARCH episode_marker_refs USING PRIMARY KEY (ref_type=? AND ref_value=?)' in plan
        assert 'episode_details' not in plan and 'SCAN e' not in plan


def test_global_transcript_lookup_scans_the_retained_by_processed_index(temp_db):
    plan = _plan(temp_db, lambda: temp_db.get_pattern_transcript_episodes({'id': 1, 'scope': 'global'}, 10))
    assert 'SCAN e USING INDEX idx_episodes_retained_by_processed' in plan and 'episode_details' not in plan \
        and 'TEMP B-TREE' not in plan


def test_podcast_transcript_lookup_uses_the_retained_by_feed_index(temp_db):
    pattern = {'id': 1, 'scope': 'podcast', 'podcast_id': SLUG}
    plan = _plan(temp_db, lambda: temp_db.get_pattern_transcript_episodes(pattern, 10))
    assert 'idx_episodes_retained_by_feed' in plan and 'TEMP B-TREE' not in plan


def test_lookups_have_no_age_limit_and_keep_newest_first(temp_db):
    old = _episode(temp_db, 1, [{'pattern_id': 7, 'sponsor': 'Acme'}], processed_at='2020-01-01T00:00:00Z')
    for n in range(2, 60):
        _episode(temp_db, n, [{'pattern_id': 8}])
    new = _episode(temp_db, 90, [{'pattern_id': 7}], processed_at='2030-01-01T00:00:00Z')
    pattern = {'id': 7, 'scope': 'global'}
    assert temp_db.get_pattern_marker_episodes(pattern, 5) == [(SLUG, new), (SLUG, old)]
    _raw_markers(temp_db, old, [{'sponsor': ' ACME '}])
    temp_db.save_episode_details(SLUG, old, ad_markers=[{'sponsor': ' ACME '}])
    assert temp_db.get_pattern_sponsor_episodes(pattern, ['acme', 'Acme '], 5) == []
    conn = temp_db.get_connection()
    conn.execute("UPDATE episode_details SET original_segments_json = '[]'")
    conn.commit()
    assert temp_db.get_pattern_sponsor_episodes(pattern, ['acme', 'Acme '], 5) == [(SLUG, old)]


def test_transcript_lookup_skips_episodes_without_segments_and_applies_the_limit(temp_db):
    eids = [_episode(temp_db, n, []) for n in range(1, 8)]
    conn = temp_db.get_connection()
    conn.execute("UPDATE episode_details SET original_segments_json = '[]' WHERE episode_id IN "
                 "(SELECT id FROM episodes WHERE episode_id != ?)", (eids[-1],))
    conn.commit()
    found = temp_db.get_pattern_transcript_episodes({'id': 1, 'scope': 'global'}, 3)
    assert found == [(SLUG, e) for e in reversed(eids[:-1])][:3]


INDEXES = ('idx_episode_marker_refs_episode', 'idx_episodes_retained_by_processed',
           'idx_episodes_retained_by_id', 'idx_episodes_retained_by_feed')
TRIGGERS = ('episode_marker_refs_update', 'episode_marker_refs_delete')


def _schema_objects(db):
    return {r[0] for r in db.get_connection().execute(
        "SELECT name FROM sqlite_master WHERE name LIKE 'idx_episode%' OR name LIKE 'episode_marker_refs%'")}


def test_fresh_database_has_the_table_indexes_and_triggers(temp_db):
    assert {'episode_marker_refs', *INDEXES, *TRIGGERS} <= _schema_objects(temp_db)


def test_existing_database_gets_the_table_indexes_and_triggers_back(temp_db, temp_dir):
    conn = temp_db.get_connection()
    for name in INDEXES:
        conn.execute(f'DROP INDEX {name}')
    for name in TRIGGERS:
        conn.execute(f'DROP TRIGGER {name}')
    conn.execute('DROP TABLE episode_marker_refs')
    conn.commit()
    reopened = object.__new__(Database)
    reopened._initialized = False
    Database.__init__(reopened, data_dir=temp_dir)
    assert {'episode_marker_refs', *INDEXES, *TRIGGERS} <= _schema_objects(reopened)


def test_a_save_with_unchanged_refs_writes_no_ref_rows(temp_db):
    eid = _episode(temp_db, 1, [{'pattern_id': 7, 'sponsor': 'Acme'}])
    conn = temp_db.get_connection()
    pk = conn.execute("SELECT id FROM episodes WHERE episode_id = ?", (eid,)).fetchone()['id']
    before = conn.total_changes
    assert replace_marker_refs(conn, pk, [{'pattern_id': 7, 'sponsor': ' acme '}, {'pattern_id': 7}]) is None
    assert conn.total_changes == before
    replace_marker_refs(conn, pk, [{'pattern_id': 7}, {'pattern_id': 8}])
    assert conn.total_changes == before + 2
    assert _refs(temp_db) == {('pattern', '7'), ('pattern', '8')}


def test_the_pending_flags_keep_a_done_answer_until_the_schema_is_registered_again(temp_db):
    assert not temp_db.marker_refs_pending()
    temp_db.get_connection().execute("DELETE FROM schema_migrations WHERE name = ?", (GATE,))
    temp_db.get_connection().commit()
    assert not temp_db.marker_refs_pending()
    temp_db._init_schema()
    assert temp_db.marker_refs_pending()


def test_sponsor_keys_are_stripped_and_lowercased():
    assert normalize_sponsor_key('  Acme Co ') == 'acme co'
