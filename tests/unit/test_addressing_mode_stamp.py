"""Per-ad addressing-mode telemetry: marker stamp, reviewer log, corrections, stats."""
from unittest.mock import MagicMock

from ad_detector import AdDetector
from ad_reviewer import AdReviewer, ReviewVerdict
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
