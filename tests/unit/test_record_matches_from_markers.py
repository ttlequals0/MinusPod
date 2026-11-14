"""Match history is written from the final markers of an episode."""
import json
import logging

import pytest

from storage import Storage
from tests.fingerprint_match_helpers import get_fingerprint_matches

SLUG = 'example-podcast'
EP = 'a1b2c3d4e5f6'


@pytest.fixture
def world(temp_db):
    temp_db.create_podcast(SLUG, 'https://example.com/feed.xml', 'Example')
    temp_db.update_podcast(SLUG, network_id='net1', tags=json.dumps(['tech']))
    temp_db.create_podcast('other', 'https://example.com/other.xml', 'Other')
    pid = temp_db.create_ad_pattern(scope='podcast', podcast_id=SLUG, text_template='acme read ' * 12)
    return temp_db, pid


def _fp(pid, start=10.0, end=55.0, **extra):
    marker = {'start': start, 'end': end, 'detection_stage': 'fingerprint', 'pattern_id': pid,
              'fingerprint_match_start': start, 'fingerprint_match_end': end,
              'fingerprint_similarity': 0.9, 'fingerprint_coverage': 0.8}
    marker.update(extra)
    return marker


def _rows(db, pid):
    return get_fingerprint_matches(db, pid, 50, 0)


def test_records_a_top_level_fingerprint_marker(world):
    db, pid = world
    assert db.record_fingerprint_matches_from_markers(SLUG, EP, [_fp(pid)]) == 1
    row, = _rows(db, pid)
    assert (row['podcast_slug'], row['episode_id'], row['match_start'], row['match_end'],
            row['similarity'], row['coverage'], row['in_scope'], row['audio_only']) == \
        (SLUG, EP, 10.0, 55.0, 0.9, 0.8, 1, 1)


def test_merged_marker_records_the_member_and_is_not_audio_only(world):
    db, pid = world
    marker = {'start': 5.0, 'end': 70.0, 'detection_stage': 'claude',
              'merged_member_spans': [
                  {'start': 10.0, 'end': 55.0, 'stage': 'fingerprint', 'pattern_id': pid,
                   'fingerprint_match_start': 12.0, 'fingerprint_match_end': 50.0,
                   'fingerprint_similarity': 0.7, 'fingerprint_coverage': 0.6},
                  {'start': 5.0, 'end': 70.0, 'stage': 'first_pass'}]}
    db.record_fingerprint_matches_from_markers(SLUG, EP, [marker])
    row, = _rows(db, pid)
    assert (row['match_start'], row['match_end'], row['similarity'], row['coverage'],
            row['audio_only']) == (12.0, 50.0, 0.7, 0.6, 0)


def test_fingerprint_only_merge_is_still_audio_only_and_not_double_counted(world):
    db, pid = world
    marker = _fp(pid, merged_member_spans=[
        {'start': 10.0, 'end': 55.0, 'stage': 'fingerprint', 'pattern_id': pid,
         'fingerprint_match_start': 10.0, 'fingerprint_match_end': 55.0}])
    db.record_fingerprint_matches_from_markers(SLUG, EP, [marker])
    row, = _rows(db, pid)
    assert row['audio_only'] == 1 and row['similarity'] is None and row['coverage'] is None


def test_member_without_match_bounds_uses_its_span(world):
    db, pid = world
    marker = {'start': 10.0, 'end': 55.0, 'detection_stage': 'fingerprint',
              'merged_member_spans': [
                  {'start': 11.0, 'end': 54.0, 'stage': 'fingerprint', 'pattern_id': pid}]}
    db.record_fingerprint_matches_from_markers(SLUG, EP, [marker])
    row, = _rows(db, pid)
    assert (row['match_start'], row['match_end']) == (11.0, 54.0)


def test_other_stages_and_unknown_patterns_record_nothing(world):
    db, pid = world
    markers = [{'start': 1.0, 'end': 9.0, 'detection_stage': 'text_pattern', 'pattern_id': pid},
               _fp(99999)]
    assert db.record_fingerprint_matches_from_markers(SLUG, EP, markers) == 0
    assert _rows(db, pid) == []


def test_reprocess_replaces_rows_of_that_episode_only(world):
    db, pid = world
    db.record_fingerprint_matches_from_markers(SLUG, 'other-ep', [_fp(pid)])
    db.record_fingerprint_matches_from_markers(SLUG, EP, [_fp(pid, 1.0, 9.0)])
    db.record_fingerprint_matches_from_markers(SLUG, EP, [_fp(pid, 20.0, 40.0)])
    spans = sorted((r['episode_id'], r['match_start']) for r in _rows(db, pid))
    assert spans == [(EP, 20.0), ('other-ep', 10.0)]


def _write_transactions(db, markers):
    conn = db.get_connection()
    statements = []
    conn.set_trace_callback(statements.append)
    try:
        db.record_fingerprint_matches_from_markers(SLUG, EP, markers)
    finally:
        conn.set_trace_callback(None)
    return [s for s in statements if s.startswith('BEGIN')]


def test_nothing_to_write_and_no_rows_opens_no_write_transaction(world):
    db, pid = world
    assert _write_transactions(db, []) == []
    assert _write_transactions(db, [_fp(987654)]) == []


def test_existing_rows_still_open_the_write_transaction(world):
    db, pid = world
    db.record_fingerprint_matches_from_markers(SLUG, EP, [_fp(pid)])
    assert _write_transactions(db, []) != []
    assert _rows(db, pid) == []


def test_a_reprocess_without_matches_clears_the_episode(world):
    db, pid = world
    db.record_fingerprint_matches_from_markers(SLUG, EP, [_fp(pid)])
    assert db.record_fingerprint_matches_from_markers(SLUG, EP, []) == 0
    assert _rows(db, pid) == []


def test_a_rejected_row_survives_a_re_record(world):
    db, pid = world
    db.record_fingerprint_matches_from_markers(
        SLUG, EP, [_fp(pid, 10.0, 55.0), _fp(pid, 100.0, 140.0)])
    assert db.mark_fingerprint_match_rejected(pid, SLUG, EP, 10.0, 55.0)
    rejected_at = next(r for r in _rows(db, pid) if r['rejected'])['rejected_at']

    assert db.record_fingerprint_matches_from_markers(
        SLUG, EP, [_fp(pid, 12.0, 54.0), _fp(pid, 100.0, 140.0), _fp(pid, 200.0, 230.0)]) == 2

    rows = sorted(_rows(db, pid), key=lambda r: r['match_start'])
    assert [(r['match_start'], r['rejected']) for r in rows] == [(10.0, 1), (100.0, 0), (200.0, 0)]
    assert rows[0]['rejected_at'] == rejected_at


def test_a_rejected_row_of_another_pattern_does_not_block(world):
    db, pid = world
    other = db.create_ad_pattern(scope='podcast', podcast_id=SLUG, text_template='other read ' * 12)
    db.record_fingerprint_matches_from_markers(SLUG, EP, [_fp(pid)])
    db.mark_fingerprint_match_rejected(pid, SLUG, EP, 10.0, 55.0)
    db.record_fingerprint_matches_from_markers(SLUG, EP, [_fp(other)])
    assert len(_rows(db, other)) == 1


def test_in_scope_follows_the_matchers_rule(world):
    db, _ = world
    elsewhere = db.create_ad_pattern(scope='podcast', podcast_id='other', text_template='a read ' * 12)
    net_ok = db.create_ad_pattern(scope='network', network_id='net1', text_template='b read ' * 12)
    net_bad = db.create_ad_pattern(scope='network', network_id='net9', text_template='c read ' * 12)
    glob = db.create_ad_pattern(scope='global', text_template='d read ' * 12)
    markers = [_fp(p, 10.0 + 100 * i, 50.0 + 100 * i)
               for i, p in enumerate((elsewhere, net_ok, net_bad, glob))]
    db.record_fingerprint_matches_from_markers(SLUG, EP, markers)
    flags = {p: _rows(db, p)[0]['in_scope'] for p in (elsewhere, net_ok, net_bad, glob)}
    assert flags == {elsewhere: 0, net_ok: 1, net_bad: 0, glob: 1}


def test_in_scope_uses_the_network_override(world):
    db, _ = world
    db.update_podcast(SLUG, network_id_override='net9')
    net_ok = db.create_ad_pattern(scope='network', network_id='net9', text_template='b read ' * 12)
    net_old = db.create_ad_pattern(scope='network', network_id='net1', text_template='c read ' * 12)
    db.record_fingerprint_matches_from_markers(
        SLUG, EP, [_fp(net_ok), _fp(net_old, 100.0, 140.0)])
    assert _rows(db, net_ok)[0]['in_scope'] == 1 and _rows(db, net_old)[0]['in_scope'] == 0


def test_community_pattern_needs_a_tag_overlap(world):
    db, _ = world
    sponsor = db.create_known_sponsor('Acme')
    db.get_connection().execute(
        "UPDATE known_sponsors SET tags = ? WHERE id = ?", (json.dumps(['cooking']), sponsor))
    db.get_connection().commit()
    pid = db.create_ad_pattern(scope='global', text_template='e read ' * 12,
                               sponsor_id=sponsor, source='community')
    db.record_fingerprint_matches_from_markers(SLUG, EP, [_fp(pid)])
    assert _rows(db, pid)[0]['in_scope'] == 0


def _status(marker, **fields):
    marker.update(fields)
    return marker


def test_only_markers_treated_as_detected_ads_are_recorded(world):
    db, _ = world
    pats = [db.create_ad_pattern(scope='global', text_template=f'{i} read ' * 12) for i in range(7)]
    cut, held, kept, rejected, uncut, legacy, marked = [
        _fp(p, 10.0 + 100 * i, 50.0 + 100 * i) for i, p in enumerate(pats)]
    _status(cut, validation={'decision': 'ACCEPT'}, was_cut=True)
    _status(held, validation={'decision': 'REVIEW'}, held_for_review=True, was_cut=False)
    _status(kept, validation={'decision': 'ACCEPT'}, action_applied='keep', was_cut=False)
    _status(rejected, validation={'decision': 'REJECT'}, was_cut=False)
    _status(uncut, validation={'decision': 'REVIEW'}, was_cut=False)
    _status(marked, validation={'decision': 'ACCEPT'}, action_applied='mark', was_cut=False)
    count = db.record_fingerprint_matches_from_markers(
        SLUG, EP, [cut, held, kept, rejected, uncut, legacy, marked])
    recorded = {p for p in pats if _rows(db, p)}
    assert recorded == {pats[0], pats[1], pats[2], pats[5], pats[6]} and count == 5


@pytest.fixture
def storage(world):
    db, pid = world
    stor = object.__new__(Storage)
    stor.db = db
    db.upsert_episode(SLUG, EP, title='Ep')
    return stor, db, pid


def test_save_combined_ads_records_the_history(storage):
    stor, db, pid = storage
    stor.save_combined_ads(SLUG, EP, [_fp(pid)])
    assert len(_rows(db, pid)) == 1


def test_a_recorder_failure_never_blocks_the_save(storage, monkeypatch, caplog):
    stor, db, pid = storage

    def boom(*a, **k):
        raise RuntimeError('db busy')
    monkeypatch.setattr(db, 'record_fingerprint_matches_from_markers', boom)
    with caplog.at_level(logging.WARNING):
        stor.save_combined_ads(SLUG, EP, [_fp(pid)])
    assert json.loads(db.get_episode(SLUG, EP)['ad_markers_json'])[0]['pattern_id'] == pid
    assert 'fingerprint match history' in caplog.text


def test_member_fields_survive_a_merge_record(world):
    from utils.markers import note_merged_members
    db, pid = world
    target = {'start': 5.0, 'end': 70.0, 'detection_stage': 'claude'}
    note_merged_members(target, _fp(pid))
    member = next(m for m in target['merged_member_spans'] if m['stage'] == 'fingerprint')
    assert member['fingerprint_similarity'] == 0.9 and member['fingerprint_coverage'] == 0.8


def test_a_rejected_row_blocks_only_a_mostly_overlapping_member(world):
    db, pid = world
    db.record_fingerprint_matches_from_markers(SLUG, EP, [_fp(pid, 10.0, 50.0)])
    db.mark_fingerprint_match_rejected(pid, SLUG, EP, 10.0, 50.0)
    assert db.record_fingerprint_matches_from_markers(
        SLUG, EP, [_fp(pid, 10.0, 50.0), _fp(pid, 49.9, 90.0)]) == 1
    starts = sorted(r['match_start'] for r in _rows(db, pid))
    assert starts == [10.0, 49.9]
    assert [r['rejected'] for r in sorted(_rows(db, pid), key=lambda r: r['match_start'])] == [1, 0]


def test_a_failed_insert_leaves_the_previous_rows(world):
    db, pid = world
    db.record_fingerprint_matches_from_markers(SLUG, EP, [_fp(pid, 10.0, 50.0), _fp(pid, 60.0, 100.0)])
    conn = db.get_connection()
    conn.execute("CREATE TRIGGER fail_third BEFORE INSERT ON audio_fingerprint_matches "
                 "WHEN NEW.match_start = 300.0 BEGIN SELECT RAISE(ABORT, 'disk full'); END")
    conn.commit()
    markers = [_fp(pid, 200.0, 240.0), _fp(pid, 250.0, 290.0), _fp(pid, 300.0, 340.0)]
    with pytest.raises(Exception, match='disk full'):
        db.record_fingerprint_matches_from_markers(SLUG, EP, markers)
    assert not conn.in_transaction
    assert sorted(r['match_start'] for r in _rows(db, pid)) == [10.0, 60.0]


def test_the_save_skips_the_recorder_when_the_episode_is_missing(storage, monkeypatch):
    stor, db, pid = storage
    calls = []
    monkeypatch.setattr(db, 'record_fingerprint_matches_from_markers', lambda *a: calls.append(a))
    stor.save_combined_ads(SLUG, 'ffffffffffff', [_fp(pid)])
    assert calls == []
