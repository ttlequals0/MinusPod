"""Markers and detection rows carry their pattern's audio state, from one query per response."""
import pytest

from tests.integration.fingerprint_helpers import EPISODE, SLUG, csrf

MARKER_COUNT = 50


@pytest.fixture
def seeded(fp_feed):
    """50 markers across the four buckets, each on its own pattern: 0 active, 1 off, the rest none."""
    ids = [fp_feed.create_ad_pattern(scope='podcast', podcast_id=SLUG, text_template=f'read number {i} ' * 8)
           for i in range(MARKER_COUNT)]
    fp_feed.create_audio_fingerprint(ids[0], b'AQAA', 6.0)
    fp_feed.create_audio_fingerprint(ids[1], b'AQAA', 6.0)
    fp_feed.set_fingerprint_enabled(ids[1], False)
    markers = []
    for i, pid in enumerate(ids):
        marker = {'start': i * 10.0, 'end': i * 10.0 + 8.0, 'pattern_id': pid, 'was_cut': True}
        if i % 4 == 1:
            marker['held_for_review'] = True
            marker['was_cut'] = False
        elif i % 4 == 2:
            marker['was_cut'] = False
        elif i % 4 == 3:
            marker['validation'] = {'decision': 'REJECT'}
        markers.append(marker)
    markers.append({'start': 900.0, 'end': 910.0, 'was_cut': True})
    fp_feed.save_episode_details(SLUG, EPISODE, ad_markers=markers)
    return ids


def _count_summary_queries(db, monkeypatch, call):
    calls, statements = [], []
    real = type(db).get_audio_states
    monkeypatch.setattr(type(db), 'get_audio_states',
                        lambda self, ids: calls.append(ids) or real(self, ids))
    conn = db.get_connection()
    conn.set_trace_callback(lambda sql: statements.append(sql) if 'audio_fingerprints' in sql else None)
    try:
        response = call()
    finally:
        conn.set_trace_callback(None)
    return response, len(calls), len(statements)


def test_episode_detail_marks_each_marker_with_one_query(app_client, fp_feed, seeded, monkeypatch):
    response, calls, statements = _count_summary_queries(
        fp_feed, monkeypatch,
        lambda: app_client.get(f'/api/v1/feeds/{SLUG}/episodes/{EPISODE}', headers=csrf(app_client)))
    body = response.get_json()
    markers = [m for key in ('adMarkers', 'rejectedAdMarkers', 'pendingReviewMarkers', 'keptMarkers')
               for m in body[key]]
    by_pattern = {m['pattern_id']: m['patternAudioState'] for m in markers if 'pattern_id' in m}
    assert len(by_pattern) == MARKER_COUNT
    assert (by_pattern[seeded[0]], by_pattern[seeded[1]], by_pattern[seeded[2]]) == ('active', 'off', 'none')
    assert all('patternAudioState' not in m for m in markers if 'pattern_id' not in m)
    assert (calls, statements) == (1, 1)


def test_detections_mark_each_row_with_one_query(app_client, fp_feed, seeded, monkeypatch):
    response, calls, statements = _count_summary_queries(
        fp_feed, monkeypatch,
        lambda: app_client.get('/api/v1/detections?status=all&limit=100', headers=csrf(app_client)))
    rows = [d for d in response.get_json()['detections'] if d['feedSlug'] == SLUG]
    states = {d['patternId']: d['patternAudioState'] for d in rows if d['patternId']}
    assert len(states) == MARKER_COUNT
    assert (states[seeded[0]], states[seeded[1]], states[seeded[2]]) == ('active', 'off', 'none')
    assert all('patternAudioState' not in d for d in rows if not d['patternId'])
    assert (calls, statements) == (1, 1)


def test_a_deleted_pattern_leaves_no_audio_state(app_client, fp_feed):
    gone = fp_feed.create_ad_pattern(scope='podcast', podcast_id=SLUG, text_template='deleted read ' * 8)
    kept = fp_feed.create_ad_pattern(scope='podcast', podcast_id=SLUG, text_template='kept read ' * 8)
    fp_feed.save_episode_details(SLUG, EPISODE, ad_markers=[
        {'start': 10.0, 'end': 18.0, 'pattern_id': gone, 'was_cut': True},
        {'start': 30.0, 'end': 38.0, 'pattern_id': kept, 'was_cut': True},
    ])
    fp_feed.delete_ad_pattern(gone)
    body = app_client.get(f'/api/v1/feeds/{SLUG}/episodes/{EPISODE}', headers=csrf(app_client)).get_json()
    by_pattern = {m['pattern_id']: m for m in body['adMarkers']}
    assert 'patternAudioState' not in by_pattern[gone]
    assert by_pattern[kept]['patternAudioState'] == 'none'
    rows = app_client.get('/api/v1/detections?status=all&limit=100', headers=csrf(app_client)).get_json()['detections']
    by_id = {d['patternId']: d for d in rows if d['feedSlug'] == SLUG}
    assert 'patternAudioState' not in by_id[gone]
    assert by_id[kept]['patternAudioState'] == 'none'
