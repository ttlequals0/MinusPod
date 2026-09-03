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
)


def _row(events, duration=3600.0):
    payload = {'version': 1, 'events': events,
               'calibration': {'status': 'cold_start'}}
    return {'episode_id': 'ep', 'original_duration': duration,
            'audio_analysis_json': json.dumps({'splice_evidence': payload})}


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
