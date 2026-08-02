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


# --- Phase 6: below_threshold + diagnostics ---------------------------------

def _phase6_records():
    return [
        {'template_id': 1, 'label': 'ding', 'cue_type': 'ad_break_boundary',
         'role': 'boundary', 'source': 'template', 'start_s': 100.0, 'end_s': 100.5,
         'match_score': 0.88, 'confidence': 0.95, 'outcome': 'snap',
         'edge_distance_s': 0.5, 'unused_reason': None},
        {'template_id': 1, 'label': 'ding', 'cue_type': 'ad_break_boundary',
         'role': 'boundary', 'source': 'template', 'start_s': 300.0, 'end_s': 300.5,
         'match_score': 0.80, 'confidence': 0.90, 'outcome': 'none',
         'edge_distance_s': 12.0, 'unused_reason': 'out_of_reach'},
        {'template_id': 1, 'label': 'ding', 'cue_type': 'ad_break_boundary',
         'role': 'boundary', 'source': 'template', 'start_s': 500.0, 'end_s': 500.5,
         'match_score': 0.72, 'confidence': None, 'outcome': 'below_threshold',
         'edge_distance_s': None, 'unused_reason': None},
        {'template_id': 1, 'label': 'ding', 'cue_type': 'ad_break_boundary',
         'role': 'boundary', 'source': 'template', 'start_s': 700.0, 'end_s': 700.5,
         'match_score': 0.70, 'confidence': None, 'outcome': 'below_threshold',
         'edge_distance_s': None, 'unused_reason': None},
    ]


def test_list_includes_diagnostic_columns(temp_db):
    pid = temp_db.create_podcast('feed', 'http://x/rss', 'Feed')
    temp_db.record_cue_detections(pid, 'ep1', _phase6_records())
    rows = temp_db.list_cue_detections_for_episode(pid, 'ep1')
    # All four rows survive, including below_threshold.
    assert len(rows) == 4
    none_row = next(r for r in rows if r['outcome'] == 'none')
    assert none_row['edge_distance_s'] == 12.0
    assert none_row['unused_reason'] == 'out_of_reach'


def test_aggregate_excludes_below_threshold_from_totals(temp_db):
    pid = temp_db.create_podcast('feed', 'http://x/rss', 'Feed')
    temp_db.record_cue_detections(pid, 'ep1', _phase6_records())
    agg = temp_db.cue_aggregate_stats()
    # total counts the 2 above-threshold rows only (snap + none), not the 2
    # below_threshold advisory rows.
    assert agg['total'] == 2
    assert agg['snapped'] == 1
    assert agg['unused'] == 1
    assert agg['nearMissTotal'] == 2
    # Near-miss histogram buckets the below_threshold scores (0.70, 0.72).
    assert any(b['count'] for b in agg['nearMissHistogram'])
    # Score histogram excludes below_threshold rows.
    hist_scores = {b['scoreFrom'] for b in agg['scoreHistogram']}
    assert 0.7 not in hist_scores


def test_aggregate_unused_reasons_breakdown(temp_db):
    pid = temp_db.create_podcast('feed', 'http://x/rss', 'Feed')
    temp_db.record_cue_detections(pid, 'ep1', _phase6_records())
    agg = temp_db.cue_aggregate_stats()
    assert agg['unusedReasons'].get('out_of_reach') == 1


def test_feed_advisory_excludes_below_threshold(temp_db):
    pid = temp_db.create_podcast('feed', 'http://x/rss', 'Feed')
    temp_db.record_cue_detections(pid, 'ep1', _phase6_records())
    adv = temp_db.cue_feed_advisory(pid)
    assert adv['total'] == 2  # below_threshold excluded


def test_labeled_scores_exclude_pending_and_below_threshold(temp_db):
    pid = temp_db.create_podcast('lfeed', 'http://x/rss', 'Feed')
    temp_db.record_cue_detections(pid, 'ep1', [
        {'template_id': 1, 'label': 'ding', 'start_s': 1, 'end_s': 2,
         'match_score': 0.9, 'outcome': 'snap'},
        {'template_id': 1, 'label': 'ding', 'start_s': 3, 'end_s': 4,
         'match_score': 0.7, 'outcome': 'none'},
        {'template_id': 1, 'label': 'ding', 'start_s': 5, 'end_s': 6,
         'match_score': 0.6, 'outcome': 'below_threshold'},
    ])
    ids = [r['id'] for r in temp_db.list_cue_detections_for_episode(pid, 'ep1')]
    temp_db.set_cue_detection_verdict(ids[0], 'confirmed')
    temp_db.set_cue_detection_verdict(ids[1], 'rejected')
    temp_db.set_cue_detection_verdict(ids[2], 'rejected')  # below_threshold row
    labeled = temp_db.cue_labeled_scores(pid)
    assert sorted(labeled) == [(0.7, 'rejected'), (0.9, 'confirmed')]


def test_cue_template_paired_episode_counts(temp_db):
    pid = temp_db.create_podcast('feed', 'http://x/rss', 'Feed')
    temp_db.record_cue_detections(pid, 'ep1', [
        {'template_id': 7, 'label': 'ding', 'start_s': 1, 'end_s': 2,
         'match_score': 0.9, 'outcome': 'pair'},
        {'template_id': 7, 'label': 'ding', 'start_s': 5, 'end_s': 6,
         'match_score': 0.9, 'outcome': 'pair'},
    ])
    temp_db.record_cue_detections(pid, 'ep2', [
        {'template_id': 7, 'label': 'ding', 'start_s': 1, 'end_s': 2,
         'match_score': 0.9, 'outcome': 'pair'},
    ])
    temp_db.record_cue_detections(pid, 'ep3', [
        {'template_id': 7, 'label': 'ding', 'start_s': 1, 'end_s': 2,
         'match_score': 0.9, 'outcome': 'snap'},
    ])
    counts = temp_db.cue_template_paired_episode_counts(pid)
    assert counts == {7: 2}


def _quiet_row(template_id, outcome='pair'):
    return {'template_id': template_id, 'label': 'ding', 'start_s': 1, 'end_s': 2,
            'match_score': 0.9, 'outcome': outcome}


def test_cue_template_recent_activity_flags_quiet(temp_db):
    pid = temp_db.create_podcast('qfeed', 'http://x/rss', 'Feed')
    for ep in ('ep1', 'ep2', 'ep3'):
        temp_db.record_cue_detections(pid, ep, [_quiet_row(template_id=7, outcome='pair')])
    for ep in ('ep4', 'ep5', 'ep6', 'ep7', 'ep8'):
        temp_db.record_cue_detections(pid, ep, [_quiet_row(template_id=9, outcome='snap')])

    # created_at has second granularity; backdate explicitly so recency
    # ordering is deterministic regardless of insert speed.
    conn = temp_db.get_connection()
    episodes = ('ep1', 'ep2', 'ep3', 'ep4', 'ep5', 'ep6', 'ep7', 'ep8')
    for i, ep in enumerate(episodes):
        conn.execute(
            "UPDATE cue_detections SET created_at = datetime('now', ?) "
            "WHERE podcast_id = ? AND episode_id = ?",
            (f'-{len(episodes) - i} minutes', pid, ep))
    conn.commit()

    activity = {a['templateId']: a for a in temp_db.cue_template_recent_activity(pid)}
    # Template 7 only appears in ep1..ep3, all outside the 5 most recent
    # episodes (ep4..ep8) -> quiet.
    assert activity[7]['quiet'] is True
    assert activity[7]['matchedEpisodes'] == 3
    assert activity[7]['lastMatchAt'] is not None
    # Template 9 appears in every one of the 5 most recent episodes -> not quiet.
    assert activity[9]['quiet'] is False
    assert activity[9]['matchedEpisodes'] == 5


def test_template_verdict_scores_grouped(temp_db):
    pid = temp_db.create_podcast('gfeed', 'http://x/rss', 'Feed')
    temp_db.record_cue_detections(pid, 'ep1', [
        {'template_id': 1, 'label': 'ding', 'start_s': 1, 'end_s': 2,
         'match_score': 0.9, 'outcome': 'snap'},
        {'template_id': 2, 'label': 'sting', 'start_s': 3, 'end_s': 4,
         'match_score': 0.8, 'outcome': 'none'},
    ])
    ids = [r['id'] for r in temp_db.list_cue_detections_for_episode(pid, 'ep1')]
    temp_db.set_cue_detection_verdict(ids[0], 'confirmed')
    temp_db.set_cue_detection_verdict(ids[1], 'rejected')
    groups = {g['templateId']: g for g in temp_db.cue_template_verdict_scores(pid)}
    assert groups[1]['confirmed'] == [0.9] and groups[1]['rejected'] == []
    assert groups[2]['rejected'] == [0.8] and groups[2]['label'] == 'sting'
