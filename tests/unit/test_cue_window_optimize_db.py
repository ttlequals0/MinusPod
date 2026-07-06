"""Unit tests for the cue_window_optimize_scans DB state machine (D2a).

Mirrors test_cue_cross_episode_scan_db.py but for the new family keyed by
template_id alone (single integer PK), not (podcast_id, episode_set_hash).
"""
import json


def _seed_template(temp_db, tag):
    """One podcast + one cue template; returns the template id."""
    pid = temp_db.create_podcast(f'wopt-feed-{tag}', f'http://x/{tag}.xml', tag.upper())
    return temp_db.create_cue_template(
        podcast_id=pid, cue_type='ad_break_boundary',
        source_episode_id=f'aabbcc00000{tag}',
        source_offset_s=5.0, duration_s=0.5,
        sample_rate=16000, n_coeffs=13,
        mfcc_blob=b'\x00' * (5 * 13 * 4),
        pcm_blob=b'\x00' * (3200 * 2),
        pcm_sample_rate=16000,
    )



def test_get_returns_none_when_absent(temp_db):
    assert temp_db.get_cue_window_optimize_scan(9999) is None


def test_claim_starts_scan(temp_db):
    tid = _seed_template(temp_db, 'a')
    assert temp_db.claim_cue_window_optimize_scan(tid, 900) == 'started'


def test_double_claim_without_rescan_returns_scanning(temp_db):
    tid = _seed_template(temp_db, 'b')
    temp_db.claim_cue_window_optimize_scan(tid, 900)
    assert temp_db.claim_cue_window_optimize_scan(tid, 900) == 'scanning'


def test_save_result_marks_ready(temp_db):
    tid = _seed_template(temp_db, 'c')
    temp_db.claim_cue_window_optimize_scan(tid, 900)
    payload = {
        'proposedStartS': 4.8,
        'proposedEndS': 5.3,
        'meanPeakScore': 0.82,
        'baselineMeanPeakScore': 0.71,
        'perEpisode': [{'episodeId': 'aabbcc000003', 'peakScore': 0.82}],
        'baselineWindow': {'startS': 5.0, 'endS': 5.5},
        'templateId': tid,
    }
    temp_db.save_cue_window_optimize_scan_result(tid, payload)
    row = temp_db.get_cue_window_optimize_scan(tid)
    assert row['status'] == 'ready'
    stored = json.loads(row['result_json'])
    assert stored['proposedStartS'] == 4.8
    assert stored['meanPeakScore'] == 0.82


def test_save_error_marks_error(temp_db):
    tid = _seed_template(temp_db, 'd')
    temp_db.claim_cue_window_optimize_scan(tid, 900)
    temp_db.save_cue_window_optimize_scan_error(tid, 'decode failed')
    row = temp_db.get_cue_window_optimize_scan(tid)
    assert row['status'] == 'error'
    assert row['error'] == 'decode failed'


def test_ready_claim_without_force_returns_ready(temp_db):
    tid = _seed_template(temp_db, 'e')
    temp_db.claim_cue_window_optimize_scan(tid, 900)
    temp_db.save_cue_window_optimize_scan_result(tid, {'proposedStartS': 5.0})
    assert temp_db.claim_cue_window_optimize_scan(tid, 900) == 'ready'


def test_rescan_on_ready_row_reclaims(temp_db):
    tid = _seed_template(temp_db, 'f')
    temp_db.claim_cue_window_optimize_scan(tid, 900)
    temp_db.save_cue_window_optimize_scan_result(tid, {'proposedStartS': 5.0})
    assert temp_db.claim_cue_window_optimize_scan(tid, 900, force=True) == 'started'


def test_fresh_error_is_not_reclaimed(temp_db):
    tid = _seed_template(temp_db, 'g')
    temp_db.claim_cue_window_optimize_scan(tid, 900)
    temp_db.save_cue_window_optimize_scan_error(tid, 'boom')
    assert temp_db.claim_cue_window_optimize_scan(tid, 900) == 'error'


def test_force_reclaims_error(temp_db):
    tid = _seed_template(temp_db, 'h')
    temp_db.claim_cue_window_optimize_scan(tid, 900)
    temp_db.save_cue_window_optimize_scan_error(tid, 'boom')
    assert temp_db.claim_cue_window_optimize_scan(tid, 900, force=True) == 'started'


def test_stale_scanning_is_reclaimable(temp_db):
    tid = _seed_template(temp_db, 'i')
    temp_db.claim_cue_window_optimize_scan(tid, 900)
    # stale_seconds=0 means the row is always stale
    assert temp_db.claim_cue_window_optimize_scan(tid, 0) == 'started'


def test_result_round_trips_all_fields(temp_db):
    tid = _seed_template(temp_db, 'j')
    temp_db.claim_cue_window_optimize_scan(tid, 900)
    payload = {
        'proposedStartS': 4.75,
        'proposedEndS': 5.25,
        'meanPeakScore': 0.91,
        'baselineMeanPeakScore': 0.68,
        'perEpisode': [
            {'episodeId': 'aabbcc00000a', 'peakScore': 0.91, 'proposedStartS': 4.75},
        ],
        'baselineWindow': {'startS': 5.0, 'endS': 5.5},
        'templateId': tid,
    }
    temp_db.save_cue_window_optimize_scan_result(tid, payload)
    row = temp_db.get_cue_window_optimize_scan(tid)
    stored = json.loads(row['result_json'])
    assert stored['proposedEndS'] == 5.25
    assert stored['baselineMeanPeakScore'] == 0.68
    assert stored['baselineWindow']['startS'] == 5.0
    assert len(stored['perEpisode']) == 1
