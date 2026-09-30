"""Per-feed splice calibration tests (spec 2.2).

Mirrors positional_prior's shape: base rates from the feed's last 5 stored
splice_evidence payloads; cold_start below the episode gate; thresholds
raised so expected content FP rate <= 1 event/hour.
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from splice_calibration import (
    build_calibration, cold_start_calibration, compute_splice_calibration,
    long_cut_corroboration,
)


def _row(events, duration=3600.0):
    payload = {'version': 1, 'events': events,
               'calibration': {'status': 'cold_start'}}
    return {'episode_id': 'ep', 'original_duration': duration,
            'audio_analysis_json': json.dumps({'splice_evidence': payload})}


def _ad_row(corroborated, total=5):
    markers = [{'start': 100.0 * i, 'end': 100.0 * i + 70.0,
                'validation': {'audio_corroboration':
                               'splice_evidence' if i < corroborated else 'none'}}
               for i in range(total)]
    markers.append({'start': 900.0, 'end': 930.0, 'validation': {'decision': 'ACCEPT'}})
    return {'episode_id': 'ep', 'original_duration': 3600.0,
            'ad_markers_json': json.dumps(markers)}


def _event(t, etype='deep_silence', duration_s=1.5):
    return {'time': t, 'end_time': t + duration_s, 'type': etype,
            'depth_dbfs': -85.0, 'duration_s': duration_s,
            'loudness_step_lu': None, 'centroid_step_hz': None,
            'flatness_step': None}


def test_five_episodes_calibrated_with_rates():
    rows = [_row([_event(100.0), _event(200.0)]) for _ in range(5)]
    cal = build_calibration(rows)
    assert cal['status'] == 'calibrated'
    assert cal['episodes_considered'] == 5
    # 10 deep_silence events over 5 hours.
    assert cal['events_per_hour']['deep_silence'] == 2.0


def test_noisy_feed_raises_duration_threshold():
    # 8 deep silences of 1.5s in each of five 1h episodes: 8/hr >> 1/hr.
    rows = [_row([_event(100.0 * i, duration_s=1.5) for i in range(1, 9)])
            for _ in range(5)]
    cal = build_calibration(rows)
    # allowed = 5 events over 5 hours; 6th-longest duration is 1.5 -> 1.6 floor.
    assert cal['thresholds']['deep_silence_min_s'] == 1.6


def test_quiet_feed_keeps_defaults():
    rows = [_row([_event(100.0)]) for _ in range(5)]  # 1 event/hour exactly
    cal = build_calibration(rows)
    assert cal['thresholds']['deep_silence_min_s'] == 1.4
    assert cal['thresholds']['digital_silence_min_s'] == 0.5


def test_four_episodes_is_cold_start():
    rows = [_row([_event(100.0)]) for _ in range(4)]
    assert build_calibration(rows)['status'] == 'cold_start'


def test_unparseable_rows_skipped():
    rows = [_row([_event(100.0)]) for _ in range(4)]
    rows.append({'episode_id': 'bad', 'original_duration': 3600.0,
                 'audio_analysis_json': 'not json'})
    assert build_calibration(rows)['status'] == 'cold_start'


def test_three_valid_episodes_cold_start_preserves_count():
    # Below the 5-episode gate: cold_start, but the real count survives so the
    # payload stays diagnosable instead of reporting a flat 0.
    rows = [_row([_event(100.0)]) for _ in range(3)]
    cal = build_calibration(rows)
    assert cal['status'] == 'cold_start'
    assert cal['episodes_considered'] == 3


def test_short_episodes_floor_keeps_detection():
    # 5 episodes of 600s (0.83h total) -> int(0.83 * 1.0) = 0; the max(1, ...)
    # floor keeps allowed=1. A single deep_silence event across the feed is
    # <= allowed, so the default 1.4 floor is kept, not zeroed to 1.6.
    rows = [_row([]) for _ in range(4)]
    rows.append(_row([_event(100.0)]))
    for row in rows:
        row['original_duration'] = 600.0
    cal = build_calibration(rows)
    assert cal['status'] == 'calibrated'
    assert cal['episodes_considered'] == 5
    assert cal['thresholds']['deep_silence_min_s'] == 1.4


def test_compute_never_raises():
    class _BoomDB:
        def get_recent_audio_analyses(self, *a, **k):
            raise RuntimeError('db down')
    cal = compute_splice_calibration(_BoomDB(), 'some-feed')
    assert cal == cold_start_calibration()


def _calibrated_rows():
    return [_row([_event(100.0)]) for _ in range(5)]


def test_long_cut_corroboration_counts_only_eligible_markers():
    rows = [_ad_row(1), _ad_row(0, total=0), {'ad_markers_json': 'not json'},
            {'ad_markers_json': None}]
    assert long_cut_corroboration(rows) == {
        'episodes': 1, 'cuts': 5, 'corroborated': 1, 'fraction': 0.2}
    assert long_cut_corroboration([])['fraction'] is None


def test_mostly_uncorroborated_long_cuts_is_host_read():
    cal = build_calibration(_calibrated_rows(), [_ad_row(1) for _ in range(5)])
    assert cal['status'] == 'host_read'
    assert cal['long_cut_corroboration']['fraction'] == 0.2


def test_mostly_corroborated_long_cuts_stays_calibrated():
    cal = build_calibration(_calibrated_rows(), [_ad_row(3) for _ in range(5)])
    assert cal['status'] == 'calibrated'


def test_too_few_episodes_with_the_field_keep_the_status():
    cal = build_calibration(_calibrated_rows(), [_ad_row(0) for _ in range(4)])
    assert cal['status'] == 'calibrated'
    assert cal['long_cut_corroboration']['episodes'] == 4


def test_cold_start_is_not_turned_into_host_read():
    rows = [_row([_event(100.0)]) for _ in range(4)]
    cal = build_calibration(rows, [_ad_row(0) for _ in range(5)])
    assert cal['status'] == 'cold_start'


class _FakeDB:
    def __init__(self, ad_rows):
        self.ad_rows = ad_rows
        self.ad_limit = None

    def get_recent_audio_analyses(self, *a, **k):
        return _calibrated_rows()

    def get_recent_episode_ad_history(self, slug, exclude_episode_id=None, limit=30):
        self.ad_limit = limit
        if isinstance(self.ad_rows, Exception):
            raise self.ad_rows
        return self.ad_rows


def test_compute_reads_the_last_twenty_episodes():
    db = _FakeDB([_ad_row(1) for _ in range(5)])
    assert compute_splice_calibration(db, 'some-feed')['status'] == 'host_read'
    assert db.ad_limit == 20


def test_compute_keeps_base_status_when_ad_history_fails():
    cal = compute_splice_calibration(_FakeDB(RuntimeError('db down')), 'some-feed')
    assert cal['status'] == 'calibrated'


def _marker(corroboration, **extra):
    return dict({'start': 100.0, 'end': 170.0,
                 'validation': {'audio_corroboration': corroboration}}, **extra)


def _rows(*marker_lists):
    return [{'episode_id': 'ep', 'original_duration': 3600.0,
             'ad_markers_json': json.dumps(markers)} for markers in marker_lists]


def test_reviewer_rejects_do_not_drag_a_feed_to_host_read():
    rejected = _marker('none', source='reviewer', was_cut=False, reviewer_verdict='reject')
    episode = ([_marker('splice_evidence')] * 3 + [_marker('none', was_cut=True)] * 2
               + [rejected] * 4)
    rows = _rows(*[episode] * 5)
    assert long_cut_corroboration(rows)['fraction'] == 0.6
    assert build_calibration(_calibrated_rows(), rows)['status'] == 'calibrated'


def test_a_reviewer_marker_that_was_cut_still_counts():
    kept = _marker('none', source='reviewer', was_cut=True)
    assert long_cut_corroboration(_rows([kept]))['cuts'] == 1


def test_fragments_of_one_detection_count_once():
    origin = {'start': 100.0, 'end': 400.0}
    fragments = [_marker('none', carved_from=origin, start=s, end=s + 80.0)
                 for s in (100.0, 200.0, 300.0)]
    other = _marker('splice_evidence', carved_from={'start': 500.0, 'end': 600.0})
    assert long_cut_corroboration(_rows(fragments + [other, _marker('splice_evidence')])) == {
        'episodes': 1, 'cuts': 3, 'corroborated': 2, 'fraction': 0.667}
