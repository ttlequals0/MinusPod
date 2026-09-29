"""A hold keeps one identity across copies, carves and reloads, and releases group by it."""

from tests.app_bootstrap import bootstrap

bootstrap('hold_identity_test_')

from ad_validator import AdValidator
from main_app import processing
from main_app.verification_reconciliation import _split_pass2_candidates_around_holds
from tests.unit.marker_test_utils import applied_cut
from tests.unit.db_test_utils import _rebuild_pre_migration_shape, _seed
from tests.unit.pass2_test_utils import (
    NO_SPLICE, _approval_db, _hold, _pair, _release_confirm, _user_corrections,
)
from utils.markers import carve_fragment, normalize_loaded_markers

def _validate(marker, confirms):
    validator = AdValidator(episode_duration=3000.0, segments=[],
                            confirmed_corrections=confirms, min_cut_confidence=0.8)
    return sorted((a['start'], a['end'], a['validation']['decision'])
                  for a in validator.validate([marker]).ads)


def _held_marker(start, end, hold_id=None):
    marker = dict(_hold(start, end), confidence=0.95, reason='Acme sponsor read',
                  detection_stage='claude')
    if hold_id:
        marker['hold_id'] = hold_id
    return marker


def test_the_validator_stamps_an_identity_when_it_holds_a_marker():
    validator = AdValidator(episode_duration=3000.0, segments=[], min_cut_confidence=0.8)
    ad = {'start': 10.0, 'end': 40.0}
    validator._mark_held(ad, [], NO_SPLICE)
    first = ad['hold_id']
    validator._mark_held(ad, [], NO_SPLICE)
    assert len(first) == 12 and ad['hold_id'] == first
    assert carve_fragment(ad, 10.0, 20.0)['hold_id'] == first
    copied = dict(ad)
    validator._mark_held(copied, [], NO_SPLICE)
    assert copied['hold_id'] == first


def test_a_legacy_hold_gets_an_identity_at_load():
    markers = normalize_loaded_markers([
        {'start': 10.0, 'end': 40.0, 'held_for_review': True, 'was_cut': False},
        {'start': 50.0, 'end': 80.0, 'was_cut': True}])
    assert len(markers[0]['hold_id']) == 12
    assert 'hold_id' not in markers[1]


def test_a_legacy_hold_gets_the_same_identity_on_every_load():
    def load():
        return normalize_loaded_markers([
            {'start': 10.0, 'end': 40.0, 'held_for_review': True, 'hold_reason': NO_SPLICE},
            {'start': 50.0, 'end': 80.0, 'held_for_review': True, 'hold_reason': NO_SPLICE}])
    first, second = load(), load()
    assert [m['hold_id'] for m in first] == [m['hold_id'] for m in second]
    assert first[0]['hold_id'] != first[1]['hold_id']


def test_a_hold_stamped_outside_the_validator_gets_an_identity_at_render():
    hold = {'start': 500.0, 'end': 560.0, 'held_for_review': True,
            'hold_reason': 'differential_uncorroborated'}
    cut = {'start': 10.0, 'end': 40.0}
    processing._finalize_cut_state([cut, hold], [cut], [applied_cut(10.0, 40.0)], 3000.0)
    assert len(hold['hold_id']) == 12
    assert 'hold_id' not in cut


def test_a_fragment_outside_a_hold_does_not_inherit_its_identity():
    proc, orig = _pair(900.0, 1110.0, hold_id='a1b2c3d4e5f6')
    _proc, fragments = _split_pass2_candidates_around_holds(
        [(proc, orig)], [_hold(1000.0, 1100.0)], [])
    assert fragments and all('hold_id' not in f for f in fragments)


def test_releases_of_a_stored_hold_whose_edges_moved_still_group():
    # Filed before and after a recut from stored markers nudged the hold's start.
    confirms = [_release_confirm((1002.0, 1200.0), (1100.0, 1150.0), hold_id='a1b2c3d4e5f6'),
                _release_confirm((1000.0, 1200.0), (1010.0, 1050.0), hold_id='a1b2c3d4e5f6')]
    got = _validate(_held_marker(1002.0, 1200.0, 'a1b2c3d4e5f6'), confirms)
    assert [(s, e) for s, e, d in got if d == 'ACCEPT'] == [(1010.0, 1050.0), (1100.0, 1150.0)]


def test_releases_without_an_identity_group_by_exact_bounds_only():
    moved = [_release_confirm((1002.0, 1200.0), (1100.0, 1150.0)),
             _release_confirm((1000.0, 1200.0), (1010.0, 1050.0))]
    got = _validate(_held_marker(1002.0, 1200.0), moved)
    assert [(s, e) for s, e, d in got if d == 'ACCEPT'] == [(1100.0, 1150.0)]
    exact = [_release_confirm((1000.0, 1200.0), (1100.0, 1150.0)),
             _release_confirm((1000.0, 1200.0), (1010.0, 1050.0))]
    got = _validate(_held_marker(1000.0, 1200.0), exact)
    assert [(s, e) for s, e, d in got if d == 'ACCEPT'] == [(1010.0, 1050.0), (1100.0, 1150.0)]


def test_a_fresh_detection_gets_a_new_identity_and_old_releases_keep_their_group():
    validator = AdValidator(episode_duration=3000.0, segments=[], min_cut_confidence=0.8)
    first, again = {'start': 1000.0, 'end': 1200.0}, {'start': 1000.0, 'end': 1200.0}
    validator._mark_held(first, [], NO_SPLICE)
    validator._mark_held(again, [], NO_SPLICE)
    assert first['hold_id'] != again['hold_id']

    # Releases filed under the old id still group with each other on the new hold.
    old = [_release_confirm((1002.0, 1200.0), (1100.0, 1150.0), hold_id=first['hold_id']),
           _release_confirm((1000.0, 1200.0), (1010.0, 1050.0), hold_id=first['hold_id'])]
    got = _validate(_held_marker(1002.0, 1200.0, again['hold_id']), old)
    assert [(s, e) for s, e, d in got if d == 'ACCEPT'] == [(1010.0, 1050.0), (1100.0, 1150.0)]


def test_filing_records_the_hold_identity(monkeypatch):
    hold = dict(_hold(1000.0, 1200.0), hold_id='a1b2c3d4e5f6', pass2_corroborated=True,
                pass2_released_spans=[{'start': 1010.0, 'end': 1050.0},
                                      {'start': 1100.0, 'end': 1150.0}],
                pass2_reviewed_release={'start': 1010.0, 'end': 1050.0})
    db = _approval_db(monkeypatch)
    assert processing._file_corroborated_hold_approvals('s', 'e', [hold], corrections=_user_corrections('s', 'e')) == 1
    assert {c.kwargs['hold_id'] for c in db.create_pattern_correction.call_args_list} == {
        'a1b2c3d4e5f6'}


# ---------- Persistence ----------

def _columns(conn):
    return sorted((r['name'], r['type'], r['notnull'], r['dflt_value'], r['pk'])
                  for r in conn.execute("PRAGMA table_info(pattern_corrections)"))


def test_confirmed_corrections_carry_the_hold_identity(temp_db):
    podcast_id, eid = _seed(temp_db, slug='hold-id-test')
    for hold_id in ('a1b2c3d4e5f6', None):
        temp_db.create_pattern_correction(
            correction_type='confirm', episode_id=eid, podcast_id=podcast_id,
            original_bounds={'start': 100.0, 'end': 200.0}, origin='auto_pass2',
            hold_id=hold_id)
    rows = temp_db.get_confirmed_corrections(podcast_id, eid)
    assert [r.get('hold_id') for r in rows] == [None, 'a1b2c3d4e5f6']


def test_hold_id_migration_is_idempotent_and_keeps_rows(temp_db):
    conn = temp_db.get_connection()
    conn.execute("ALTER TABLE pattern_corrections DROP COLUMN hold_id")
    conn.execute("INSERT INTO pattern_corrections (correction_type, text_snippet) "
                 "VALUES ('confirm', 'kept row')")
    conn.commit()
    temp_db._run_schema_migrations()
    temp_db._run_schema_migrations()
    assert 'hold_id' in {r['name'] for r in conn.execute(
        "PRAGMA table_info(pattern_corrections)")}
    assert tuple(conn.execute("SELECT text_snippet, hold_id FROM pattern_corrections").fetchone()) == (
        'kept row', None)


def test_upgraded_schema_matches_a_fresh_one(temp_db):
    conn = temp_db.get_connection()
    fresh = _columns(conn)
    _rebuild_pre_migration_shape(conn)
    conn.execute("INSERT INTO ad_patterns (scope, text_template, sponsor) "
                 "VALUES ('global', 'ad text', 'Acme')")
    conn.execute("INSERT INTO pattern_corrections (pattern_id, correction_type) "
                 "VALUES (1, 'confirm')")
    conn.commit()
    temp_db._run_schema_migrations()
    assert _columns(conn) == fresh
    assert conn.execute("SELECT COUNT(*) FROM pattern_corrections").fetchone()[0] == 1


def test_sponsor_fk_rebuild_keeps_hold_id(temp_db):
    conn = temp_db.get_connection()
    _rebuild_pre_migration_shape(conn)
    conn.execute("INSERT INTO ad_patterns (scope, text_template, sponsor) "
                 "VALUES ('global', 'ad text', 'Acme')")
    conn.execute("ALTER TABLE pattern_corrections ADD COLUMN hold_id TEXT")
    conn.execute("INSERT INTO pattern_corrections (pattern_id, correction_type, hold_id) "
                 "VALUES (1, 'confirm', 'a1b2c3d4e5f6')")
    conn.commit()
    temp_db._migrate_sponsor_fk(conn)
    assert conn.execute(
        "SELECT hold_id FROM pattern_corrections").fetchone()['hold_id'] == 'a1b2c3d4e5f6'
