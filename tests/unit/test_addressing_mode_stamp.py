"""Per-ad addressing-mode telemetry: marker stamp, reviewer log, corrections, stats."""
from unittest.mock import MagicMock

from ad_detector import AdDetector, dai_differential_ads, transcript_differential_ads
from text_pattern_matcher import TextMatch
from ad_reviewer import AdReviewer, ReviewVerdict
from tests.unit.db_test_utils import _rebuild_pre_migration_shape
from tests.unit.test_cue_fusion import _diff, _region
from tests.unit.test_segment_id_addressing import _run_window

_TS_RESPONSE = ('[{"start": 10.0, "end": 20.0, "confidence": 0.9, '
                '"category": "sponsor", "reason": "x"}]')
_ID_RESPONSE = ('[{"start_id": 0, "end_id": 0, "confidence": 0.9, '
                '"category": "sponsor", "reason": "x"}]')


def test_timestamps_ad_is_stamped_with_mode_and_presnap_bounds():
    result = _run_window(AdDetector(api_key='k'), addressing_mode='timestamps',
                         response_text=_TS_RESPONSE)
    ad = result.ads[0]
    assert ad['addressing_mode'] == 'timestamps'
    assert (ad['llm_start'], ad['llm_end']) == (ad['start'], ad['end'])


def test_segment_id_ad_is_stamped_with_its_own_mode():
    result = _run_window(AdDetector(api_key='k'), addressing_mode='segment_ids',
                         with_sid=True, response_text=_ID_RESPONSE)
    ad = result.ads[0]
    assert ad['addressing_mode'] == 'segment_ids'
    assert ad['llm_start'] == ad['start']


def test_reviewer_flush_log_writes_mode(temp_db):
    reviewer = AdReviewer(db=temp_db, llm_client=MagicMock())
    verdicts = [
        ReviewVerdict(pool='accepted', pass_num=1, verdict='confirmed',
                      original_start=1.0, original_end=2.0, model_used='m',
                      addressing_mode='segment_ids'),
        ReviewVerdict(pool='accepted', pass_num=1, verdict='confirmed',
                      original_start=3.0, original_end=4.0, model_used='m'),
    ]
    reviewer._flush_log(verdicts, {'episode_id': 'ep', 'podcast_id': None})
    rows = temp_db.get_connection().execute(
        "SELECT addressing_mode FROM ad_reviewer_log ORDER BY original_start"
    ).fetchall()
    assert [r['addressing_mode'] for r in rows] == ['segment_ids', None]


def test_pattern_correction_stores_mode(temp_db):
    cid = temp_db.create_pattern_correction(
        correction_type='confirm', episode_id='ep', addressing_mode='timestamps')
    row = temp_db.get_connection().execute(
        "SELECT addressing_mode FROM pattern_corrections WHERE id = ?", (cid,)
    ).fetchone()
    assert row['addressing_mode'] == 'timestamps'


def _seed_review(db, mode, verdict, adjusted=False):
    conn = db.get_connection()
    conn.execute(
        """INSERT INTO ad_reviewer_log
           (episode_id, pass, pool, original_start, original_end, verdict,
            adjusted_start, adjusted_end, model_used, success, addressing_mode)
           VALUES ('ep', 1, 'accepted', 10.0, 20.0, ?, ?, ?, 'm', 1, ?)""",
        (verdict, 12.0 if adjusted else None, 22.0 if adjusted else None, mode))
    conn.commit()


def test_reviewer_stats_by_mode_excludes_null_rows(temp_db):
    _seed_review(temp_db, 'segment_ids', 'adjust', adjusted=True)
    _seed_review(temp_db, 'segment_ids', 'confirmed')
    _seed_review(temp_db, None, 'adjust', adjusted=True)
    by_mode = temp_db.get_reviewer_stats()['byMode']
    assert by_mode['segment_ids']['adjusted'] == 1
    assert by_mode['segment_ids']['reviews'] == 2
    assert by_mode['segment_ids']['avgBoundaryShiftSeconds'] == 2.0
    assert by_mode['timestamps']['reviews'] == 0
    assert by_mode['timestamps']['avgBoundaryShiftSeconds'] is None


def test_addressing_stats_by_mode(temp_db):
    for ctype, hold in (('confirm', 'x'), ('false_positive', None),
                        ('boundary_adjustment', None), ('confirm', None)):
        temp_db.create_pattern_correction(
            correction_type=ctype, episode_id='ep', addressing_mode='segment_ids',
            source_hold_reason=hold)
    temp_db.create_pattern_correction(correction_type='confirm', episode_id='ep')
    stats = temp_db.get_addressing_stats()
    seg = stats['byMode']['segment_ids']
    assert (seg['heldRate'], seg['approveRate'], seg['rejectRate']) == (0.25, 0.75, 0.25)
    assert seg['avgSnapDistance'] is None
    assert stats['byMode']['timestamps']['approveRate'] is None


def test_avg_snap_distance_reads_stored_markers(temp_db):
    temp_db.create_podcast('show', 'http://example.com/f.xml', 'Show')
    temp_db.upsert_episode('show', 'ep1', title='T',
                           original_url='http://example.com/a.mp3')
    markers = [
        {'start': 12.0, 'end': 22.0, 'llm_start': 10.0, 'llm_end': 20.0,
         'addressing_mode': 'segment_ids'},
        {'start': 5.0, 'end': 6.0},
    ]
    temp_db.save_episode_details('show', 'ep1', ad_markers=markers)
    stats = temp_db.get_addressing_stats()
    assert stats['byMode']['segment_ids']['avgSnapDistance'] == 4.0
    assert stats['byMode']['timestamps']['avgSnapDistance'] is None


def _set_processed_at(db, episode_id, value):
    conn = db.get_connection()
    conn.execute("UPDATE episodes SET processed_at = ? WHERE episode_id = ?",
                 (value, episode_id))
    conn.commit()


def test_avg_snap_distance_excludes_episodes_outside_the_window(temp_db):
    temp_db.create_podcast('show', 'http://example.com/f.xml', 'Show')
    for ep, start in (('recent', 12.0), ('old', 100.0)):
        temp_db.upsert_episode('show', ep, title='T',
                               original_url='http://example.com/a.mp3')
        temp_db.save_episode_details('show', ep, ad_markers=[
            {'start': start, 'end': start + 10, 'llm_start': 10.0, 'llm_end': 20.0,
             'addressing_mode': 'timestamps'}])
    _set_processed_at(temp_db, 'recent', '2999-01-01T00:00:00Z')
    _set_processed_at(temp_db, 'old', '2000-01-01T00:00:00Z')
    stats = temp_db.get_addressing_stats()
    assert stats['byMode']['timestamps']['avgSnapDistance'] == 4.0


def test_non_llm_ads_carry_no_mode_fields():
    ads = dai_differential_ads(_diff(_region(100.0, 140.0, 0.2)), [])
    ads += transcript_differential_ads([{'start': 5.0, 'end': 25.0}])
    assert len(ads) == 2
    for ad in ads:
        assert not {'addressing_mode', 'llm_start', 'llm_end'} & ad.keys()
    ads, regions = [], []
    AdDetector(api_key='k')._add_pattern_match(
        TextMatch(pattern_id=7, start=30.0, end=60.0, confidence=0.9),
        'text_pattern', 'content', ads, regions, 'a1b2c3d4e5f6')
    assert len(ads) == 1
    assert not {'addressing_mode', 'llm_start', 'llm_end'} & ads[0].keys()


def test_correction_row_survives_sponsor_fk_rebuild_with_null_mode(temp_db):
    conn = temp_db.get_connection()
    _rebuild_pre_migration_shape(conn)
    conn.execute("INSERT INTO pattern_corrections (correction_type, text_snippet) "
                 "VALUES ('confirm', 'kept row')")
    conn.commit()
    temp_db._run_schema_migrations()
    row = conn.execute(
        "SELECT text_snippet, addressing_mode FROM pattern_corrections").fetchone()
    assert (row['text_snippet'], row['addressing_mode']) == ('kept row', None)
