"""Unit tests for the cue_detections telemetry mixin (#350 follow-up)."""
import pytest


def _records():
    return [
        {'template_id': 1, 'label': 'ding', 'cue_type': 'ad_break_boundary',
         'role': 'boundary', 'source': 'template', 'start_s': 100.0, 'end_s': 100.5,
         'match_score': 0.88, 'confidence': 0.95, 'outcome': 'pair'},
        {'template_id': 1, 'label': 'ding', 'cue_type': 'ad_break_boundary',
         'role': 'boundary', 'source': 'template', 'start_s': 300.0, 'end_s': 300.5,
         'match_score': 0.80, 'confidence': 0.90, 'outcome': 'none'},
    ]


def test_record_and_list(temp_db):
    pid = temp_db.create_podcast('feed', 'http://x/rss', 'Feed')
    n = temp_db.record_cue_detections(pid, 'ep1', _records())
    assert n == 2
    rows = temp_db.list_cue_detections_for_episode(pid, 'ep1')
    assert [r['start_s'] for r in rows] == [100.0, 300.0]
    assert all(r['verdict'] == 'pending' for r in rows)


def test_record_replaces_on_reprocess(temp_db):
    pid = temp_db.create_podcast('feed', 'http://x/rss', 'Feed')
    temp_db.record_cue_detections(pid, 'ep1', _records())
    temp_db.record_cue_detections(pid, 'ep1', _records()[:1])
    assert len(temp_db.list_cue_detections_for_episode(pid, 'ep1')) == 1


def test_set_verdict_and_advisory(temp_db):
    pid = temp_db.create_podcast('feed', 'http://x/rss', 'Feed')
    temp_db.record_cue_detections(pid, 'ep1', _records())
    det_id = temp_db.list_cue_detections_for_episode(pid, 'ep1')[0]['id']
    assert temp_db.set_cue_detection_verdict(det_id, 'confirmed')
    adv = temp_db.cue_feed_advisory(pid)
    assert adv['total'] == 2 and adv['paired'] == 1 and adv['confirmed'] == 1
    assert adv['confirmRate'] == 1.0


def test_invalid_verdict_rejected(temp_db):
    pid = temp_db.create_podcast('feed', 'http://x/rss', 'Feed')
    temp_db.record_cue_detections(pid, 'ep1', _records()[:1])
    det_id = temp_db.list_cue_detections_for_episode(pid, 'ep1')[0]['id']
    with pytest.raises(ValueError):
        temp_db.set_cue_detection_verdict(det_id, 'bogus')


def test_aggregate_histogram(temp_db):
    pid = temp_db.create_podcast('feed', 'http://x/rss', 'Feed')
    temp_db.record_cue_detections(pid, 'ep1', _records())
    agg = temp_db.cue_aggregate_stats()
    assert agg['total'] == 2
    assert any(b['count'] for b in agg['scoreHistogram'])
