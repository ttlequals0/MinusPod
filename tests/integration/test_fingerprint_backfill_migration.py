"""One-shot backfill of audio match history from the markers already stored on episodes."""
import json
import logging
import re
import shutil
import threading
from datetime import datetime, timezone

import pytest

import database.fingerprint_matches as fm
import database.schema.deferred as deferred
from database import Database
from utils.markers import parse_ad_markers
from tests.fingerprint_match_helpers import record_fingerprint_match

GATE = 'backfill_fingerprint_matches_v1'
SLUG = 'example-podcast'
OTHER = 'other-podcast'
EP1 = 'a1b2c3d4e5f6'
EP2 = 'b2c3d4e5f6a1'
EP_BAD = 'c3d4e5f6a1b2'
EP_ORPHAN = 'd4e5f6a1b2c3'
ROW_COLUMNS = ('pattern_id', 'episode_id', 'podcast_slug', 'match_start', 'match_end',
               'similarity', 'coverage', 'in_scope', 'audio_only')


def _open(path):
    db = object.__new__(Database)
    db._initialized = False
    Database.__init__(db, data_dir=str(path))
    return db


def _fp(pid, start, end, **extra):
    marker = {'start': start, 'end': end, 'detection_stage': 'fingerprint', 'pattern_id': pid,
              'fingerprint_match_start': start + 1.0, 'fingerprint_match_end': end - 2.0,
              'fingerprint_similarity': 0.9, 'fingerprint_coverage': 0.8}
    marker.update(extra)
    return marker


def _put_markers(db, slug, episode_id, markers, processed_at=None, raw=None):
    db.upsert_episode(slug, episode_id, original_url='https://example.com/e.mp3', title='T',
                      status='processed')
    db.save_episode_details(slug, episode_id, ad_markers=markers)
    conn = db.get_connection()
    if raw is not None:
        conn.execute("UPDATE episode_details SET ad_markers_json = ? WHERE episode_id = "
                     "(SELECT id FROM episodes WHERE episode_id = ?)", (raw, episode_id))
    conn.execute("UPDATE episodes SET processed_at = ? WHERE episode_id = ?", (processed_at, episode_id))
    conn.commit()


def _restart(path, db=None):
    """Forget the backfill and open the database again, as the next start would."""
    if db is not None:
        conn = db.get_connection()
        conn.execute("DELETE FROM schema_migrations WHERE name = ?", (GATE,))
        conn.commit()
    db = _open(path)
    db.run_deferred_upgrade_steps()
    return db


def _rows(db, where='1'):
    cursor = db.get_connection().execute(
        f"SELECT * FROM audio_fingerprint_matches WHERE {where} ORDER BY id")  # noqa: S608
    return [dict(r) for r in cursor.fetchall()]


def _shape(rows):
    return sorted(tuple(r[c] for c in ROW_COLUMNS) for r in rows)


@pytest.fixture
def seeded(tmp_path):
    """A database with stored markers and no match history, ready for the next start."""
    db = _open(tmp_path)
    db.create_podcast(SLUG, 'https://example.com/a.xml', 'Example Podcast')
    db.create_podcast(OTHER, 'https://example.com/b.xml', 'Other Podcast')
    mine = db.create_ad_pattern(scope='podcast', podcast_id=SLUG, text_template='mine read ' * 8,
                                created_from_episode_id=EP1)
    theirs = db.create_ad_pattern(scope='podcast', podcast_id=OTHER, text_template='theirs read ' * 8,
                                  created_from_episode_id='f6a1b2c3d4e5')
    glob = db.create_ad_pattern(scope='global', text_template='global read ' * 8)
    db.create_audio_fingerprint(mine, b'AQAA', 57.0)
    db.create_audio_fingerprint(theirs, b'AQAA', 40.0)
    db.create_audio_fingerprint(glob, b'AQAA', 30.0, source_podcast_slug=SLUG, source_episode_id='keepme00000a',
                                source_start=3.0, source_end=33.0)
    merged = {'start': 295.0, 'end': 360.0, 'detection_stage': 'claude', 'merged_member_spans': [
        {'start': 300.0, 'end': 350.0, 'stage': 'fingerprint', 'pattern_id': theirs},
        {'start': 295.0, 'end': 360.0, 'stage': 'first_pass'}]}
    rejected = _fp(glob, 500.0, 560.0, validation={'decision': 'REJECT'})
    markers = [
        _fp(mine, 100.0, 160.0),
        merged,
        rejected,
        _fp(99999, 600.0, 650.0),
        {'start': 700.0, 'end': 760.0, 'detection_stage': 'first_pass'},
    ]
    _put_markers(db, SLUG, EP1, markers, processed_at='2026-10-01T10:00:00Z')
    _put_markers(db, OTHER, EP2, [_fp(theirs, 20.0, 70.0, fingerprint_similarity=None)], processed_at=None)
    podcast_id = db.get_podcast_by_slug(SLUG)['id']
    db.create_pattern_correction(
        correction_type='false_positive', pattern_id=mine, episode_id=EP1,
        original_bounds={'start': 105.0, 'end': 155.0}, podcast_id=podcast_id)
    db.get_connection().execute(
        "UPDATE pattern_corrections SET created_at = '2026-10-05T08:00:00Z'")
    db.get_connection().commit()
    return {'path': tmp_path, 'db': db, 'mine': mine, 'theirs': theirs, 'glob': glob, 'markers': markers}


def test_backfill_writes_the_recorders_rows(seeded):
    db = _restart(seeded['path'], seeded['db'])
    rows = _rows(db)
    assert {(r['pattern_id'], r['episode_id']) for r in rows} == {
        (seeded['mine'], EP1), (seeded['theirs'], EP1), (seeded['theirs'], EP2)}
    db.get_connection().execute("DELETE FROM audio_fingerprint_matches")
    db.get_connection().commit()
    for slug, ep in ((SLUG, EP1), (OTHER, EP2)):
        raw = db.get_connection().execute(
            "SELECT d.ad_markers_json FROM episode_details d JOIN episodes e ON e.id = d.episode_id "
            "WHERE e.episode_id = ?", (ep,)).fetchone()[0]
        db.record_fingerprint_matches_from_markers(slug, ep, parse_ad_markers(raw))
    assert _shape(_rows(db)) == _shape(rows)


def test_row_fields(seeded):
    db = _restart(seeded['path'], seeded['db'])
    own = _rows(db, f"pattern_id = {seeded['mine']}")[0]
    assert (own['match_start'], own['match_end'], own['similarity'], own['coverage']) == (101.0, 158.0, 0.9, 0.8)
    assert (own['in_scope'], own['audio_only'], own['podcast_slug']) == (1, 1, SLUG)
    foreign = _rows(db, f"pattern_id = {seeded['theirs']} AND episode_id = '{EP1}'")[0]
    assert (foreign['in_scope'], foreign['audio_only']) == (0, 0)
    assert (foreign['similarity'], foreign['coverage']) == (None, None)
    assert (foreign['match_start'], foreign['match_end']) == (300.0, 350.0)
    assert foreign['rejected'] == 0 and foreign['rejected_at'] is None


def test_rows_carry_the_slug_and_skip_rejected_missing_and_orphaned(seeded):
    conn = seeded['db'].get_connection()
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("INSERT INTO episodes (podcast_id, episode_id, original_url) VALUES (424242, ?, 'u')",
                 (EP_ORPHAN,))
    conn.execute("INSERT INTO episode_details (episode_id, ad_markers_json) "
                 "SELECT id, ? FROM episodes WHERE episode_id = ?",
                 (json.dumps([_fp(seeded['mine'], 1.0, 50.0)]), EP_ORPHAN))
    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")
    db = _restart(seeded['path'], seeded['db'])
    assert {r['podcast_slug'] for r in _rows(db)} == {SLUG, OTHER}
    assert not _rows(db, f"episode_id = '{EP_ORPHAN}'")
    assert not _rows(db, f"pattern_id = {seeded['glob']}")
    assert not _rows(db, "pattern_id = 99999")


def test_created_at_is_the_processed_time_else_the_migration_time(seeded):
    before = datetime.now(timezone.utc).replace(microsecond=0)
    db = _restart(seeded['path'], seeded['db'])
    assert {r['created_at'] for r in _rows(db, f"episode_id = '{EP1}'")} == {'2026-10-01T10:00:00Z'}
    stamp = _rows(db, f"episode_id = '{EP2}'")[0]['created_at']
    assert re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ', stamp)
    assert datetime.strptime(stamp, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc) >= before


def test_a_rejection_overlapping_a_row_marks_it_rejected(seeded):
    db = _restart(seeded['path'], seeded['db'])
    own = _rows(db, f"pattern_id = {seeded['mine']}")[0]
    assert (own['rejected'], own['rejected_at']) == (1, '2026-10-05T08:00:00Z')
    assert all(r['rejected'] == 0 for r in _rows(db, f"pattern_id != {seeded['mine']}"))


def test_rejections_that_do_not_overlap_or_belong_elsewhere_are_ignored(seeded):
    db = seeded['db']
    podcast_id = db.get_podcast_by_slug(SLUG)['id']
    db.get_connection().execute("DELETE FROM pattern_corrections")
    db.get_connection().commit()
    for pattern_id, episode, bounds, kind in (
            (seeded['mine'], EP1, {'start': 150.0, 'end': 400.0}, 'false_positive'),
            (seeded['mine'], EP2, {'start': 105.0, 'end': 155.0}, 'false_positive'),
            (seeded['glob'], EP1, {'start': 105.0, 'end': 155.0}, 'false_positive'),
            (seeded['mine'], EP1, {'start': 105.0, 'end': 155.0}, 'confirm')):
        db.create_pattern_correction(correction_type=kind, pattern_id=pattern_id, episode_id=episode,
                                     original_bounds=bounds, podcast_id=podcast_id)
    assert all(r['rejected'] == 0 for r in _rows(_restart(seeded['path'], db)))


def test_a_second_start_changes_nothing(seeded, caplog):
    db = _restart(seeded['path'], seeded['db'])
    first = _rows(db)
    assert first
    db.get_connection().execute("DELETE FROM audio_fingerprint_matches WHERE id = ?", (first[0]['id'],))
    db.get_connection().commit()
    caplog.clear()
    with caplog.at_level(logging.INFO):
        again = _open(seeded['path'])
        again.run_deferred_upgrade_steps()
    assert _rows(again) == first[1:]
    assert not [r for r in caplog.records if GATE in r.getMessage() and 'finished' in r.getMessage()]


def test_calling_the_step_twice_adds_nothing(seeded):
    db = _restart(seeded['path'], seeded['db'])
    before = _rows(db)
    db.run_deferred_upgrade_steps()
    assert _rows(db) == before


def test_two_workers_starting_together_write_one_set(seeded):
    conn = seeded['db'].get_connection()
    conn.execute("DELETE FROM schema_migrations WHERE name = ?", (GATE,))
    conn.commit()
    gate, errors = threading.Barrier(2), []

    def start():
        try:
            gate.wait()
            _open(seeded['path']).run_deferred_upgrade_steps()
        except Exception as exc:
            errors.append(exc)
    workers = [threading.Thread(target=start) for _ in range(2)]
    for w in workers:
        w.start()
    for w in workers:
        w.join()
    assert not errors
    reader = _open(seeded['path'])
    assert len(_rows(reader)) == 3
    assert reader.get_connection().execute(
        "SELECT COUNT(*) FROM schema_migrations WHERE name = ?", (GATE,)).fetchone()[0] == 1


def test_episodes_that_already_have_history_are_left_alone(seeded):
    db = seeded['db']
    record_fingerprint_match(db, seeded['mine'], EP1, SLUG, 7.0, 9.0, 0.5, 0.5, True, False)
    rows = _rows(_restart(seeded['path'], db), f"episode_id = '{EP1}'")
    assert [(r['match_start'], r['match_end']) for r in rows] == [(7.0, 9.0)]


def test_corrupt_markers_are_skipped_with_one_warning_naming_only_the_row_id(seeded, caplog):
    _put_markers(seeded['db'], SLUG, EP_BAD, None, raw='[{"detection_stage": "fingerprint", ')
    row_id = seeded['db'].get_connection().execute(
        "SELECT id FROM episodes WHERE episode_id = ?", (EP_BAD,)).fetchone()[0]
    with caplog.at_level(logging.WARNING):
        db = _restart(seeded['path'], seeded['db'])
    warnings = [r for r in caplog.records
                if r.levelno == logging.WARNING and 'fingerprint' in r.getMessage().lower()
                and 'markers' in r.getMessage().lower()]
    assert len(warnings) == 1
    message = warnings[0].getMessage()
    assert str(row_id) in message and EP_BAD not in message and SLUG not in message
    assert len(_rows(db)) == 3
    assert db.get_connection().execute("SELECT 1 FROM schema_migrations WHERE name = ?", (GATE,)).fetchone()


def test_an_info_line_reports_rows_and_seconds(seeded, caplog):
    with caplog.at_level(logging.INFO):
        _restart(seeded['path'], seeded['db'])
    lines = [r.getMessage() for r in caplog.records if GATE in r.getMessage() and 'finished' in r.getMessage()]
    assert len(lines) == 1 and re.search(r'3 row', lines[0]) and re.search(r'\d+\.\d+ ?s', lines[0])


def test_a_failed_write_rolls_back_everything_and_retries_on_the_next_start(seeded, caplog):
    conn = seeded['db'].get_connection()
    conn.execute("DELETE FROM schema_migrations WHERE name = ?", (GATE,))
    conn.execute("""CREATE TRIGGER fail_third BEFORE INSERT ON audio_fingerprint_matches
        WHEN (SELECT COUNT(*) FROM audio_fingerprint_matches) >= 2
        BEGIN SELECT RAISE(ABORT, 'forced failure'); END""")
    conn.commit()
    with caplog.at_level(logging.WARNING):
        db = _open(seeded['path'])
        assert db.run_deferred_upgrade_steps() is False
    errors = [r for r in caplog.records if r.levelno == logging.WARNING and 'failed with' in r.getMessage()]
    assert len(errors) == 1
    assert _rows(db) == []
    assert db.get_connection().execute("SELECT 1 FROM schema_migrations WHERE name = ?", (GATE,)).fetchone() is None
    assert db.get_connection().execute(
        "SELECT source_episode_id FROM audio_fingerprints WHERE pattern_id = ?", (seeded['mine'],)
    ).fetchone()[0] is None
    db.get_connection().execute("DROP TRIGGER fail_third")
    db.get_connection().commit()
    assert len(_rows(_restart(seeded['path']))) == 3


def test_a_read_failure_is_logged_and_startup_continues(seeded, monkeypatch, caplog):
    def boom(markers):
        raise RuntimeError('selection failed')
    monkeypatch.setattr(fm, 'plan_fingerprint_matches', boom)
    with caplog.at_level(logging.WARNING):
        db = _restart(seeded['path'], seeded['db'])
    assert len([r for r in caplog.records if r.levelno == logging.WARNING
                and 'failed with RuntimeError' in r.getMessage()]) == 1
    assert _rows(db) == []
    assert db.get_connection().execute("SELECT 1 FROM schema_migrations WHERE name = ?", (GATE,)).fetchone() is None


def _without_rejections(seeded):
    seeded['db'].get_connection().execute("DELETE FROM pattern_corrections")
    seeded['db'].get_connection().commit()


def test_a_missing_source_comes_from_the_origin_episodes_match(seeded):
    _without_rejections(seeded)
    db = _restart(seeded['path'], seeded['db'])
    mine = db.get_audio_fingerprint(seeded['mine'])
    assert (mine['source_podcast_slug'], mine['source_episode_id'],
            mine['source_start'], mine['source_end']) == (SLUG, EP1, 101.0, 158.0)


def test_sources_are_left_empty_without_a_reliable_origin(seeded):
    _without_rejections(seeded)
    db = _restart(seeded['path'], seeded['db'])
    theirs = db.get_audio_fingerprint(seeded['theirs'])
    assert (theirs['source_podcast_slug'], theirs['source_episode_id'],
            theirs['source_start'], theirs['source_end']) == (None, None, None, None)
    kept = db.get_audio_fingerprint(seeded['glob'])
    assert (kept['source_episode_id'], kept['source_start'], kept['source_end']) == ('keepme00000a', 3.0, 33.0)


def test_an_implausibly_long_or_short_span_is_not_taken_as_the_source(seeded):
    _without_rejections(seeded)
    seeded['db'].get_connection().execute(
        "UPDATE audio_fingerprints SET duration = 5.0 WHERE pattern_id = ?", (seeded['mine'],))
    seeded['db'].get_connection().commit()
    db = _restart(seeded['path'], seeded['db'])
    assert db.get_audio_fingerprint(seeded['mine'])['source_episode_id'] is None


def test_a_rejected_match_is_not_the_source(seeded):
    db = _restart(seeded['path'], seeded['db'])
    assert _rows(db, f"pattern_id = {seeded['mine']}")[0]['rejected'] == 1
    assert db.get_audio_fingerprint(seeded['mine'])['source_episode_id'] is None


def test_nothing_is_deleted(seeded):
    before = {t: seeded['db'].get_connection().execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]  # noqa: S608
              for t in ('audio_fingerprints', 'ad_patterns', 'episodes', 'episode_details',
                        'pattern_corrections', 'podcasts')}
    db = _restart(seeded['path'], seeded['db'])
    after = {t: db.get_connection().execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]  # noqa: S608
             for t in before}
    assert after == before


def test_an_empty_database_just_sets_the_mark(tmp_path):
    db = _open(tmp_path)
    assert db.get_connection().execute("SELECT 1 FROM schema_migrations WHERE name = ?", (GATE,)).fetchone()
    assert _rows(db) == []


EP_DEMOTED = 'e5f6a1b2c3d4'
EP_PLAIN = 'f6a1b2c3d4e5'
EP_SHARED = 'a1b2c3d4e5f7'


def _demoted(marker):
    """The stored shape of a held marker after Not this ad."""
    return {**marker, 'held_for_review': False, 'was_cut': False, 'validation': {'decision': 'REJECT'}}


def _correct(db, pattern_id, episode_id, bounds, slug=SLUG, created_at='2026-10-06T09:30:00Z'):
    db.create_pattern_correction(
        correction_type='false_positive', pattern_id=pattern_id, episode_id=episode_id,
        original_bounds=bounds, podcast_id=db.get_podcast_by_slug(slug)['id'])
    db.get_connection().execute(
        "UPDATE pattern_corrections SET created_at = ? WHERE episode_id = ?", (created_at, episode_id))
    db.get_connection().commit()


def test_a_demoted_marker_with_a_matching_correction_gives_one_rejected_row(seeded):
    db = seeded['db']
    marker = _demoted(_fp(seeded['mine'], 800.0, 860.0))
    _put_markers(db, SLUG, EP_DEMOTED, [marker], processed_at='2026-10-02T10:00:00Z')
    _correct(db, seeded['mine'], EP_DEMOTED, {'start': 805.0, 'end': 855.0})
    rows = _rows(_restart(seeded['path'], db), f"episode_id = '{EP_DEMOTED}'")
    assert len(rows) == 1
    assert (rows[0]['pattern_id'], rows[0]['rejected'], rows[0]['rejected_at']) == (
        seeded['mine'], 1, '2026-10-06T09:30:00Z')
    assert (rows[0]['match_start'], rows[0]['match_end']) == (801.0, 858.0)


def test_a_demoted_merged_member_with_a_matching_correction_gives_one_rejected_row(seeded):
    db = seeded['db']
    marker = _demoted({'start': 800.0, 'end': 870.0, 'detection_stage': 'claude', 'merged_member_spans': [
        {'start': 810.0, 'end': 860.0, 'stage': 'fingerprint', 'pattern_id': seeded['mine']},
        {'start': 800.0, 'end': 870.0, 'stage': 'first_pass'}]})
    _put_markers(db, SLUG, EP_DEMOTED, [marker])
    _correct(db, seeded['mine'], EP_DEMOTED, {'start': 812.0, 'end': 858.0})
    rows = _rows(_restart(seeded['path'], db), f"episode_id = '{EP_DEMOTED}'")
    assert [(r['rejected'], r['match_start'], r['match_end']) for r in rows] == [(1, 810.0, 860.0)]


def test_a_demoted_marker_without_a_correction_gives_no_row(seeded):
    db = seeded['db']
    _put_markers(db, SLUG, EP_PLAIN, [_demoted(_fp(seeded['mine'], 800.0, 860.0))])
    _correct(db, seeded['mine'], EP_PLAIN, {'start': 100.0, 'end': 120.0})
    assert not _rows(_restart(seeded['path'], db), f"episode_id = '{EP_PLAIN}'")


def test_a_correction_for_another_pattern_does_not_revive_a_demoted_marker(seeded):
    db = seeded['db']
    _put_markers(db, SLUG, EP_PLAIN, [_demoted(_fp(seeded['mine'], 800.0, 860.0))])
    _correct(db, seeded['glob'], EP_PLAIN, {'start': 805.0, 'end': 855.0})
    assert not _rows(_restart(seeded['path'], db), f"episode_id = '{EP_PLAIN}'")


def test_the_recorder_still_skips_a_demoted_marker(seeded):
    db = seeded['db']
    marker = _demoted(_fp(seeded['mine'], 800.0, 860.0))
    assert db.record_fingerprint_matches_from_markers(SLUG, EP_DEMOTED, [marker]) == 0
    assert not _rows(db, f"episode_id = '{EP_DEMOTED}'")


def test_a_correction_with_an_unreadable_time_is_rejected_at_the_migration_time(seeded):
    before = datetime.now(timezone.utc).replace(microsecond=0)
    seeded['db'].get_connection().execute("UPDATE pattern_corrections SET created_at = 'not a date'")
    seeded['db'].get_connection().commit()
    own = _rows(_restart(seeded['path'], seeded['db']), f"pattern_id = {seeded['mine']}")[0]
    assert own['rejected'] == 1
    stamp = datetime.strptime(own['rejected_at'], '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
    assert stamp >= before


def test_an_open_caller_transaction_is_not_committed_by_the_read(seeded):
    conn = seeded['db'].get_connection()
    conn.execute("INSERT INTO schema_migrations (name) VALUES ('pending_caller_work')")
    assert conn.in_transaction
    seeded['db']._read_stored_match_history()
    assert conn.in_transaction
    conn.rollback()
    assert not conn.execute("SELECT 1 FROM schema_migrations WHERE name = 'pending_caller_work'").fetchone()


def _shared_episode_pattern(db, scope='global', podcast_id=None):
    pid = db.create_ad_pattern(scope=scope, podcast_id=podcast_id, text_template='shared read ' * 8,
                               created_from_episode_id=EP_SHARED)
    db.create_audio_fingerprint(pid, b'AQAA', 57.0)
    return pid


def test_a_source_is_left_empty_when_two_feeds_share_the_origin_episode_id(seeded):
    _without_rejections(seeded)
    db = seeded['db']
    pid = _shared_episode_pattern(db)
    _put_markers(db, SLUG, EP_SHARED, [_fp(pid, 100.0, 160.0)])
    _put_markers(db, OTHER, EP_SHARED, [_fp(pid, 100.0, 160.0)])
    assert _restart(seeded['path'], db).get_audio_fingerprint(pid)['source_episode_id'] is None


def test_a_global_pattern_takes_the_only_feed_holding_the_origin_episode(seeded):
    _without_rejections(seeded)
    db = seeded['db']
    pid = _shared_episode_pattern(db)
    _put_markers(db, SLUG, EP_SHARED, [_fp(pid, 100.0, 160.0)])
    source = _restart(seeded['path'], db).get_audio_fingerprint(pid)
    assert (source['source_podcast_slug'], source['source_episode_id']) == (SLUG, EP_SHARED)


def test_a_podcast_pattern_takes_its_own_feeds_copy_of_a_shared_episode_id(seeded):
    _without_rejections(seeded)
    db = seeded['db']
    pid = _shared_episode_pattern(db, scope='podcast', podcast_id=OTHER)
    _put_markers(db, SLUG, EP_SHARED, [_fp(pid, 100.0, 160.0)])
    _put_markers(db, OTHER, EP_SHARED, [_fp(pid, 100.0, 160.0)])
    source = _restart(seeded['path'], db).get_audio_fingerprint(pid)
    assert (source['source_podcast_slug'], source['source_episode_id']) == (OTHER, EP_SHARED)


def test_the_log_line_names_patterns_with_out_of_scope_rows(seeded, caplog):
    with caplog.at_level(logging.INFO):
        _restart(seeded['path'], seeded['db'])
    line = [r.getMessage() for r in caplog.records if GATE in r.getMessage() and 'finished' in r.getMessage()][0]
    assert re.search(r'out of scope[^\[]*\[([^\]]*)\]', line).group(1) == str(seeded['theirs'])


def test_the_log_line_caps_the_listed_pattern_ids(seeded, caplog):
    db = seeded['db']
    ids = [db.create_ad_pattern(scope='podcast', podcast_id=OTHER, text_template=f'other read {i} ' * 8)
           for i in range(52)]
    for pid in ids:
        db.create_audio_fingerprint(pid, b'AQAA', 60.0)
    _put_markers(db, SLUG, EP_PLAIN, [_fp(pid, 10.0 + i * 100, 70.0 + i * 100) for i, pid in enumerate(ids)])
    with caplog.at_level(logging.INFO):
        _restart(seeded['path'], db)
    line = [r.getMessage() for r in caplog.records if GATE in r.getMessage() and 'finished' in r.getMessage()][0]
    listed = re.search(r'out of scope[^\[]*\[([^\]]*)\]', line).group(1).split(',')
    assert len(listed) == 50
    assert re.search(r'and 3 more', line)


@pytest.mark.parametrize('demoted', [False, True])
def test_a_correction_rejects_every_overlapping_member_of_a_merged_marker(seeded, demoted):
    db = seeded['db']
    marker = {'start': 800.0, 'end': 900.0, 'detection_stage': 'claude', 'merged_member_spans': [
        {'start': 805.0, 'end': 855.0, 'stage': 'fingerprint', 'pattern_id': seeded['mine']},
        {'start': 806.0, 'end': 854.0, 'stage': 'fingerprint', 'pattern_id': seeded['glob']},
        {'start': 870.0, 'end': 900.0, 'stage': 'fingerprint', 'pattern_id': seeded['theirs']}]}
    _put_markers(db, SLUG, EP_DEMOTED, [_demoted(marker) if demoted else marker])
    _correct(db, seeded['mine'], EP_DEMOTED, {'start': 805.0, 'end': 855.0})
    rows = _rows(_restart(seeded['path'], db), f"episode_id = '{EP_DEMOTED}'")
    flags = {r['pattern_id']: r['rejected'] for r in rows}
    assert flags[seeded['mine']] == flags[seeded['glob']] == 1
    assert flags.get(seeded['theirs'], 0) == 0
    assert (seeded['theirs'] in flags) != demoted


class Killed(BaseException):
    """A worker killed between two pages; nothing in the step may catch it."""


def _copy_of(seeded, name):
    seeded['db'].get_connection().execute("PRAGMA wal_checkpoint(TRUNCATE)")
    target = seeded['path'] / name
    shutil.copytree(seeded['path'], target, ignore=shutil.ignore_patterns('copy*', 'kill*'))
    return target


def _drop_mark(path):
    db = _open(path)
    db.get_connection().execute("DELETE FROM schema_migrations WHERE name = ?", (GATE,))
    db.get_connection().commit()
    return _open(path)


def _sources(db):
    return sorted(tuple(r) for r in db.get_connection().execute(
        "SELECT pattern_id, source_podcast_slug, source_episode_id, source_start, source_end "
        "FROM audio_fingerprints"))


def test_a_worker_killed_at_any_page_boundary_is_finished_by_the_next_claim(seeded, monkeypatch):
    _without_rejections(seeded)
    monkeypatch.setattr(deferred, 'BACKFILL_PAGE_EPISODES', 1)
    reference = _drop_mark(_copy_of(seeded, 'copy_ref'))
    pages = []
    reference.run_deferred_upgrade_steps(pause=lambda: pages.append(1))
    assert len(pages) >= 2
    expected = (_shape(_rows(reference)), _sources(reference))
    for kill_at in range(1, len(pages) + 1):
        db = _drop_mark(_copy_of(seeded, f'kill{kill_at}'))
        seen = []

        def pause():
            seen.append(1)
            if len(seen) >= kill_at:
                raise Killed
        with pytest.raises(Killed):
            db.run_deferred_upgrade_steps(clock=lambda: 50.0, pause=pause)
        db.get_connection().rollback()
        assert db.history_backfill_pending()
        assert db.run_deferred_upgrade_steps(clock=lambda: 51.0)
        assert db.history_backfill_pending()
        assert db.run_deferred_upgrade_steps(clock=lambda: 50.0 + deferred.STALE_HEARTBEAT_SECONDS + 1)
        assert not db.history_backfill_pending()
        assert (_shape(_rows(db)), _sources(db)) == expected, kill_at


def test_each_page_is_its_own_short_transaction(seeded, monkeypatch):
    monkeypatch.setattr(deferred, 'BACKFILL_PAGE_EPISODES', 1)
    db = _drop_mark(_copy_of(seeded, 'copy_pages'))
    commits = []
    real = type(db).transaction

    def counting(self, *args, **kwargs):
        commits.append(1)
        return real(self, *args, **kwargs)
    monkeypatch.setattr(type(db), 'transaction', counting)
    db.run_deferred_upgrade_steps()
    assert len(commits) >= 4


def test_no_fingerprints_skips_the_scan(seeded, monkeypatch):
    conn = seeded['db'].get_connection()
    conn.execute("DELETE FROM audio_fingerprints")
    conn.execute("DELETE FROM schema_migrations WHERE name = ?", (GATE,))
    conn.commit()
    db = _open(seeded['path'])

    def fail(*_args, **_kwargs):
        raise AssertionError('scanned the markers')
    monkeypatch.setattr(type(db), '_read_stored_match_history', fail)
    assert db.run_deferred_upgrade_steps()
    assert not db.history_backfill_pending() and _rows(db) == []


def test_a_fresh_install_marks_the_step_done_without_a_claim(tmp_path):
    db = _open(tmp_path)
    assert not db.history_backfill_pending()
    assert db.get_connection().execute(
        "SELECT state FROM deferred_upgrade_steps WHERE name = ?", (GATE,)).fetchone()[0] == 'done'


def test_the_pending_flag_follows_the_step(seeded):
    db = _drop_mark(_copy_of(seeded, 'copy_flag'))
    assert db.history_backfill_pending()
    db.run_deferred_upgrade_steps()
    assert not db.history_backfill_pending()


def _after_read(monkeypatch, db, action, once=True):
    """Run action on another connection right after a page was read, before it is written."""
    real = type(db)._read_stored_match_history
    fired = []

    def hooked(self, *args, **kwargs):
        out = real(self, *args, **kwargs)
        if out[1] is not None and not (once and fired):
            fired.append(1)
            worker = threading.Thread(target=action)
            worker.start()
            worker.join()
        return out
    monkeypatch.setattr(type(db), '_read_stored_match_history', hooked)
    return fired


def _reject(db, pattern_id, episode_id, slug, bounds):
    pid = db.get_podcast_by_slug(slug)['id']
    db.create_pattern_correction(correction_type='false_positive', pattern_id=pattern_id, episode_id=episode_id,
                                 original_bounds=bounds, podcast_id=pid)


def _prepared(seeded, name, monkeypatch):
    monkeypatch.setattr(deferred, 'BACKFILL_PAGE_EPISODES', 1)
    _without_rejections(seeded)
    return _drop_mark(_copy_of(seeded, name))


def test_a_rejection_made_during_the_run_marks_the_row_when_its_page_is_reached(seeded, monkeypatch):
    db = _prepared(seeded, 'copy_rej_live', monkeypatch)
    pages = []

    def pause():
        pages.append(1)
        if len(pages) == 1:
            _reject(db, seeded['theirs'], EP2, OTHER, {'start': 20.0, 'end': 70.0})
    assert db.run_deferred_upgrade_steps(pause=pause)
    rows = _rows(db, f"pattern_id = {seeded['theirs']} AND episode_id = '{EP2}'")
    assert [r['rejected'] for r in rows] == [1] and rows[0]['rejected_at']


def test_a_rejection_made_between_the_page_read_and_write_is_not_lost(seeded, monkeypatch):
    db = _prepared(seeded, 'copy_rej_window', monkeypatch)
    fired = _after_read(monkeypatch, db, lambda: _reject(db, seeded['mine'], EP1, SLUG,
                                                         {'start': 105.0, 'end': 155.0}))
    assert db.run_deferred_upgrade_steps() and fired
    assert [r['rejected'] for r in _rows(db, f"pattern_id = {seeded['mine']}")] == [1]


def test_an_undone_rejection_does_not_stay_rejected(seeded, monkeypatch):
    monkeypatch.setattr(deferred, 'BACKFILL_PAGE_EPISODES', 1)
    _without_rejections(seeded)
    _reject(seeded['db'], seeded['theirs'], EP2, OTHER, {'start': 20.0, 'end': 70.0})
    db = _drop_mark(_copy_of(seeded, 'copy_rej_undone'))
    pages = []

    def pause():
        pages.append(1)
        if len(pages) == 1:
            db.get_connection().execute("DELETE FROM pattern_corrections")
            db.get_connection().commit()
    assert db.run_deferred_upgrade_steps(pause=pause)
    assert [r['rejected'] for r in _rows(db, f"pattern_id = {seeded['theirs']} AND episode_id = '{EP2}'")] == [0]


def test_a_rejection_undone_between_the_page_read_and_write_is_not_kept(seeded, monkeypatch):
    monkeypatch.setattr(deferred, 'BACKFILL_PAGE_EPISODES', 1)
    _without_rejections(seeded)
    _reject(seeded['db'], seeded['mine'], EP1, SLUG, {'start': 105.0, 'end': 155.0})
    db = _drop_mark(_copy_of(seeded, 'copy_rej_undone_window'))

    def undo():
        db.get_connection().execute("DELETE FROM pattern_corrections")
        db.get_connection().commit()
    fired = _after_read(monkeypatch, db, undo)
    assert db.run_deferred_upgrade_steps() and fired
    assert [r['rejected'] for r in _rows(db, f"pattern_id = {seeded['mine']}")] == [0]


def test_an_episode_deleted_during_the_page_gets_no_history_rows(seeded, monkeypatch):
    db = _prepared(seeded, 'copy_ep_deleted', monkeypatch)

    def delete_episode():
        conn = db.get_connection()
        conn.execute("DELETE FROM episodes WHERE episode_id = ?", (EP1,))
        conn.commit()
    fired = _after_read(monkeypatch, db, delete_episode)
    assert db.run_deferred_upgrade_steps() and fired
    assert not _rows(db, f"episode_id = '{EP1}'")
    assert _rows(db, f"episode_id = '{EP2}'")


def test_a_pattern_whose_audio_was_deleted_during_the_run_gets_no_history_rows(seeded, monkeypatch):
    db = _prepared(seeded, 'copy_audio_deleted', monkeypatch)

    def delete_audio():
        conn = db.get_connection()
        conn.execute("DELETE FROM audio_fingerprints WHERE pattern_id = ?", (seeded['theirs'],))
        conn.commit()
    fired = _after_read(monkeypatch, db, delete_audio, once=False)
    assert db.run_deferred_upgrade_steps() and fired
    assert not _rows(db, f"pattern_id = {seeded['theirs']}")
    assert _rows(db, f"pattern_id = {seeded['mine']}")


def test_a_step_whose_owner_lost_the_claim_cannot_mark_itself_done(seeded, monkeypatch):
    db = _drop_mark(_copy_of(seeded, 'copy_lost_claim'))

    def lose_then_finish(self, cursor, save, ctx):
        conn = self.get_connection()
        conn.execute("UPDATE deferred_upgrade_steps SET owner = 'someone-else' WHERE name = ?", (GATE,))
        conn.commit()
        return None
    monkeypatch.setattr(type(db), '_advance_backfill', lose_then_finish)
    assert db.run_deferred_upgrade_steps()
    assert db.history_backfill_pending()


def test_every_origin_page_heartbeats_before_the_next_read(seeded, monkeypatch):
    db = _drop_mark(_copy_of(seeded, 'copy_heartbeat'))
    ticks = iter(range(100, 10_000))
    last = []

    def clock():
        last.append(next(ticks))
        return float(last[-1])
    seen = []
    real = type(db)._origin_candidate_rows

    def spy(self, after, limit):
        seen.append((self.get_connection().execute(
            "SELECT heartbeat_at FROM deferred_upgrade_steps WHERE name = ?", (GATE,)).fetchone()[0], last[-1]))
        return real(self, after, limit)
    monkeypatch.setattr(deferred, 'ORIGIN_PAGE_PATTERNS', 1)
    monkeypatch.setattr(type(db), '_origin_candidate_rows', spy)
    assert db.run_deferred_upgrade_steps(clock=clock)
    assert len(seen) >= 2 and all(beat == float(tick) for beat, tick in seen)


def _unsourced_patterns(db, count):
    """Patterns with a fingerprint and an origin episode holding a match, so each has a source to find."""
    ids = []
    for i in range(count):
        episode = f'{i:012x}'
        pid = db.create_ad_pattern(scope='global', text_template=f'origin read {i} ' * 8,
                                   created_from_episode_id=episode)
        db.create_audio_fingerprint(pid, b'AQAA', 57.0)
        _put_markers(db, SLUG, episode, [_fp(pid, 100.0, 160.0)], processed_at='2026-10-01T10:00:00Z')
        ids.append(pid)
    return ids


def test_the_paged_origin_phase_gives_the_unpaged_result(seeded, monkeypatch):
    _without_rejections(seeded)
    _unsourced_patterns(seeded['db'], 5)
    unpaged = _drop_mark(_copy_of(seeded, 'copy_unpaged'))
    unpaged.run_deferred_upgrade_steps()
    monkeypatch.setattr(deferred, 'ORIGIN_PAGE_PATTERNS', 2)
    paged = _drop_mark(_copy_of(seeded, 'copy_paged'))
    pages = []
    assert paged.run_deferred_upgrade_steps(pause=lambda: pages.append(1))
    assert _sources(paged) == _sources(unpaged)
    assert sum(1 for row in _sources(paged) if row[2]) >= 5


def test_the_origin_phase_holds_at_most_a_page_of_patterns(seeded, monkeypatch):
    _without_rejections(seeded)
    _unsourced_patterns(seeded['db'], 5)
    db = _drop_mark(_copy_of(seeded, 'copy_bounded'))
    monkeypatch.setattr(deferred, 'ORIGIN_PAGE_PATTERNS', 2)
    limits, sizes = [], []
    real = type(db)._origin_candidate_rows

    def spy(self, after, limit):
        limits.append(limit)
        rows, last = real(self, after, limit)
        sizes.append(len({r[0] for r in rows}))
        return rows, last
    monkeypatch.setattr(type(db), '_origin_candidate_rows', spy)
    assert db.run_deferred_upgrade_steps()
    assert set(limits) == {2} and max(sizes) <= 2 and len(limits) >= 3


def test_a_worker_killed_at_any_origin_page_boundary_is_finished_by_the_next_claim(seeded, monkeypatch):
    _without_rejections(seeded)
    _unsourced_patterns(seeded['db'], 4)
    monkeypatch.setattr(deferred, 'ORIGIN_PAGE_PATTERNS', 1)
    reference = _drop_mark(_copy_of(seeded, 'copy_origin_ref'))
    pages = []
    reference.run_deferred_upgrade_steps(pause=lambda: pages.append(1))
    expected = _sources(reference)
    for kill_at in range(1, len(pages) + 1):
        db = _drop_mark(_copy_of(seeded, f'kill_origin{kill_at}'))
        seen = []

        def pause():
            seen.append(1)
            if len(seen) >= kill_at:
                raise Killed
        with pytest.raises(Killed):
            db.run_deferred_upgrade_steps(clock=lambda: 50.0, pause=pause)
        db.get_connection().rollback()
        assert db.run_deferred_upgrade_steps(clock=lambda: 50.0 + deferred.STALE_HEARTBEAT_SECONDS + 1)
        assert not db.history_backfill_pending()
        assert _sources(db) == expected, kill_at


def _correction_without_a_time(seeded, created_at):
    _without_rejections(seeded)
    _reject(seeded['db'], seeded['mine'], EP1, SLUG, {'start': 105.0, 'end': 155.0})
    conn = seeded['db'].get_connection()
    conn.execute("UPDATE pattern_corrections SET created_at = ?", (created_at,))
    conn.commit()


@pytest.mark.parametrize('created_at', [None, 'not a date'])
def test_a_correction_without_a_readable_time_is_computed_once_with_one_fallback(seeded, monkeypatch, created_at):
    _correction_without_a_time(seeded, created_at)
    monkeypatch.setattr(deferred, 'BACKFILL_PAGE_EPISODES', 1000)
    db = _drop_mark(_copy_of(seeded, 'copy_untimed'))
    reads = []
    real = type(db)._read_stored_match_history

    def counting(self, *args, **kwargs):
        reads.append(1)
        return real(self, *args, **kwargs)
    monkeypatch.setattr(type(db), '_read_stored_match_history', counting)
    assert db.run_deferred_upgrade_steps()
    assert len(reads) == 2
    stamps = {r['rejected_at'] for r in _rows(db) if r['rejected']}
    assert len(stamps) == 1 and re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ', stamps.pop())


def test_a_real_correction_change_still_recomputes_when_another_has_no_time(seeded, monkeypatch):
    _correction_without_a_time(seeded, None)
    db = _drop_mark(_copy_of(seeded, 'copy_untimed_change'))
    reads = []
    real = type(db)._read_stored_match_history

    def counting(self, *args, **kwargs):
        reads.append(1)
        return real(self, *args, **kwargs)
    monkeypatch.setattr(type(db), '_read_stored_match_history', counting)
    _after_read(monkeypatch, db, lambda: _reject(db, seeded['theirs'], EP2, OTHER, {'start': 20.0, 'end': 70.0}))
    assert db.run_deferred_upgrade_steps()
    assert [r['rejected'] for r in _rows(db, f"pattern_id = {seeded['theirs']} AND episode_id = '{EP2}'")] == [1]
