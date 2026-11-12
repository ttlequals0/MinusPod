"""Every API route that returns a stored error column redacts it at the serializer, always."""
import hashlib
import json

import pytest

# The helpers import bootstraps the app environment, so it comes before the app modules.
from tests.integration.fingerprint_helpers import EPISODE, SLUG, csrf
from api import error_response, get_database
from database.error_text import STORED_ERROR_COLUMNS
from main_app import feeds as feed_refresh

KEY = 'b' * 64
RAW = f'GET https://alice:PASS@cdn.example.com/a.mp3?X-Amz-Signature=SIG failed {KEY}'
LEAKS = ('PASS', 'SIG', KEY, 'alice')


def _sql(db, statement, params=()):
    conn = db.get_connection()
    conn.execute(statement, params)
    conn.commit()


@pytest.fixture(autouse=True)
def _remove_seeded_rows(app_client):
    """The shared database outlives a test, so drop the rows these seeds insert outside the feed."""
    yield
    for table, column in (('pattern_cleanup_runs', 'error'), ('failover_events', 'reason'),
                          ('processing_history', 'error_message')):
        _sql(get_database(), f"DELETE FROM {table} WHERE {column} = ?", (RAW,))  # noqa: S608


def _episode_column(column):
    def seed(db):
        _sql(db, f"UPDATE episodes SET {column} = ? WHERE episode_id = ?", (RAW, EPISODE))  # noqa: S608
    return seed


def _youtube_sb_error(db):
    _sql(db, "UPDATE podcasts SET feed_type = 'youtube' WHERE slug = ?", (SLUG,))
    _sql(db, "UPDATE episodes SET sb_last_error = ? WHERE episode_id = ?", (RAW, EPISODE))


def _history_error(db):
    pid = db.get_podcast_by_slug(SLUG)['id']
    _sql(db, "INSERT INTO processing_history (podcast_id, podcast_slug, podcast_title, episode_id, "
             "episode_title, status, error_message) VALUES (?, ?, 't', ?, 'e', 'failed', ?)",
         (pid, SLUG, EPISODE, RAW))


def _refresh_error(db):
    _sql(db, "UPDATE podcasts SET refresh_failure_count = 99, last_refresh_error = ? WHERE slug = ?",
         (RAW, SLUG))


def _cleanup_error(db):
    _sql(db, "INSERT INTO pattern_cleanup_runs (status, error, started_at, finished_at) "
             "VALUES ('failed', ?, '2026-01-01T00:00:00Z', '2026-01-01T00:01:00Z')", (RAW,))


def _failover_reason(db):
    _sql(db, "INSERT INTO failover_events (target, action, source, reason) VALUES ('llm', 'trigger', 'auto', ?)",
         (RAW,))


def _scan_error(table, key_col, key):
    def seed(db):
        pid = db.get_podcast_by_slug(SLUG)['id']
        cols = f"podcast_id, {key_col}" if table != 'cue_window_optimize_scans' else key_col
        marks = '?, ?' if table != 'cue_window_optimize_scans' else '?'
        args = (pid, key) if table != 'cue_window_optimize_scans' else (key,)
        _sql(db, f"INSERT INTO {table} ({cols}, status, error) VALUES ({marks}, 'error', ?)",  # noqa: S608
             (*args, RAW))
    return seed


def _episodes(client):
    return client.get(f'/api/v1/feeds/{SLUG}/episodes', headers=csrf(client))


def _episode(client):
    return client.get(f'/api/v1/feeds/{SLUG}/episodes/{EPISODE}', headers=csrf(client))


def _get(url):
    return lambda client: client.get(url, headers=csrf(client))


# Stored error column -> (seed, request that serves it). auto_process_queue.error_message has no route.
SERVED = {
    ('episodes', 'error_message'): (_episode_column('error_message'), _episodes),
    ('episodes', 'chapters_regen_error'): (_episode_column('chapters_regen_error'), _episode),
    ('episodes', 'sb_last_error'): (_youtube_sb_error, _episode),
    ('processing_history', 'error_message'): (_history_error, _get('/api/v1/history')),
    ('podcasts', 'last_refresh_error'): (_refresh_error, _get(f'/api/v1/feeds/{SLUG}')),
    ('pattern_cleanup_runs', 'error'): (_cleanup_error, _get('/api/v1/patterns/cleanup/runs')),
    ('failover_events', 'reason'): (_failover_reason, _get('/api/v1/failover')),
    ('episodes', 'detection_degraded'): (_episode_column('detection_degraded'), _episode),
}
NOT_SERVED = {('auto_process_queue', 'error_message')}
CUE_SCANS = {
    ('cue_candidate_scans', 'error'): (
        _scan_error('cue_candidate_scans', 'episode_id', EPISODE),
        lambda client: client.get(f'/api/v1/feeds/{SLUG}/episodes/{EPISODE}/cue-candidates',
                                  headers=csrf(client))),
    ('cue_threshold_scans', 'error'): (
        _scan_error('cue_threshold_scans', 'episode_id', EPISODE),
        lambda client: client.post(f'/api/v1/feeds/{SLUG}/cue-threshold-suggest',
                                   json={'episodeId': EPISODE}, headers=csrf(client))),
}


def test_every_stored_error_column_is_classified():
    classified = set(SERVED) | NOT_SERVED | set(CUE_SCANS) | {
        ('cue_cross_episode_scans', 'error'), ('cue_window_optimize_scans', 'error')}
    assert classified == set(STORED_ERROR_COLUMNS)


def _assert_no_leak(response):
    assert response.status_code == 200, response.get_data(as_text=True)
    body = response.get_data(as_text=True)
    assert 'cdn.example.com' in body
    for leak in LEAKS:
        assert leak not in body


@pytest.mark.parametrize('column', sorted({**SERVED, **CUE_SCANS}), ids=lambda c: '.'.join(c))
def test_a_stored_error_is_redacted_on_the_way_out(app_client, fp_feed, column):
    seed, request = {**SERVED, **CUE_SCANS}[column]
    seed(fp_feed)
    _assert_no_leak(request(app_client))


@pytest.mark.parametrize('export_format', ['csv', 'json'])
def test_history_exports_redact_the_error_and_stay_streamed(app_client, fp_feed, export_format):
    _history_error(fp_feed)
    response = app_client.get(f'/api/v1/history/export?format={export_format}', headers=csrf(app_client))
    assert response.is_streamed
    _assert_no_leak(response)


def test_cross_episode_scan_error_is_redacted(app_client, fp_feed):
    ids = [EPISODE, 'b1c2d3e4f5a6']
    digest = hashlib.sha256(','.join(sorted(ids)).encode()).hexdigest()
    _scan_error('cue_cross_episode_scans', 'episode_set_hash', digest)(fp_feed)
    response = app_client.post(f'/api/v1/feeds/{SLUG}/cue-cross-episode-scan',
                               json={'episodeIds': ids}, headers=csrf(app_client))
    _assert_no_leak(response)


def test_window_optimize_scan_error_is_redacted(app_client, fp_feed):
    pid = fp_feed.get_podcast_by_slug(SLUG)['id']
    template = fp_feed.create_cue_template(pid, 'intro', EPISODE, 0.0, 1.0, 16000, 13, b'x')
    _scan_error('cue_window_optimize_scans', 'template_id', template)(fp_feed)
    response = app_client.post(f'/api/v1/feeds/{SLUG}/cue-templates/{template}/optimize-window',
                               json={}, headers=csrf(app_client))
    _assert_no_leak(response)


def test_dai_differential_error_is_redacted(app_client, fp_feed):
    conn = fp_feed.get_connection()
    row_id = conn.execute("SELECT id FROM episodes WHERE episode_id = ?", (EPISODE,)).fetchone()[0]
    conn.execute("INSERT INTO episode_details (episode_id, dai_differential_json) VALUES (?, ?) "
                 "ON CONFLICT(episode_id) DO UPDATE SET dai_differential_json = excluded.dai_differential_json",
                 (row_id, json.dumps({'status': 'error', 'error': RAW})))
    conn.commit()
    _assert_no_leak(_episode(app_client))


@pytest.mark.parametrize('key,url', [
    ('youtube_ytdlp_last_error', '/api/v1/settings/youtube'),
    ('db_backup_last_error', '/api/v1/settings/db-backup'),
    ('community_sync_last_error', '/api/v1/settings/community-sync'),
])
def test_a_last_error_setting_is_redacted_on_the_way_out(app_client, fp_feed, key, url):
    _sql(fp_feed, "INSERT INTO settings (key, value, is_default) VALUES (?, ?, 0) "
                  "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, RAW))
    try:
        _assert_no_leak(_get(url)(app_client))
    finally:
        fp_feed.clear_setting(key)


def test_failover_state_and_probe_text_are_redacted_on_the_way_out(app_client, fp_feed):
    _sql(fp_feed, "INSERT INTO settings (key, value, is_default) VALUES ('failover_state:llm:primary', ?, 0) "
                  "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
         (json.dumps({'active': True, 'source': 'auto', 'since': 'x', 'reason': RAW}),))
    _sql(fp_feed, "INSERT INTO settings (key, value, is_default) VALUES ('failover_probe:llm:primary', ?, 0) "
                  "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
         (json.dumps({'reachable': False, 'detail': RAW}),))
    try:
        _assert_no_leak(_get('/api/v1/failover')(app_client))
    finally:
        fp_feed.clear_setting('failover_state:llm:primary')
        fp_feed.clear_setting('failover_probe:llm:primary')


def test_error_response_redacts_message_and_details(app_client):
    with app_client.application.app_context():
        response = error_response(RAW, 400, details=RAW)
    assert response.status_code == 400
    body = response.get_data(as_text=True)
    assert 'cdn.example.com' in body
    assert not any(leak in body for leak in LEAKS)


def _seed_run(db, stats):
    pid = db.get_podcast_by_slug(SLUG)['id']
    _sql(db, "INSERT INTO processing_history (podcast_id, podcast_slug, podcast_title, episode_id, "
             "episode_title, status, error_message, processing_stats_json) "
             "VALUES (?, ?, 't', ?, 'e', 'failed', ?, ?)", (pid, SLUG, EPISODE, RAW, json.dumps(stats)))


@pytest.mark.parametrize('url', [
    f'/api/v1/feeds/{SLUG}/episodes/{EPISODE}',
    f'/api/v1/stats/episode-costs/runs?slug={SLUG}&episodeId={EPISODE}',
])
def test_processing_runs_redact_the_history_error_and_stats_text(app_client, fp_feed, url):
    _seed_run(fp_feed, {'chapters_degraded': True, 'chapters_degraded_reason': RAW,
                        'transcription': {'outcome': 'failed', 'error': RAW}})
    response = _get(url)(app_client)
    _assert_no_leak(response)
    body = response.get_data(as_text=True)
    assert 'errorMessage' in body and 'chaptersDegradedReason' in body


def test_partial_detection_reason_is_redacted(app_client, fp_feed):
    _episode_column('detection_degraded')(fp_feed)
    response = _episode(app_client)
    _assert_no_leak(response)
    assert response.get_json()['partialDetection']['reason']


def _raising_outcome(*args, **kwargs):
    return feed_refresh.RefreshOutcome(False, 'fetch_failed', error=RAW)


def test_single_feed_refresh_redacts_the_outcome_error(app_client, fp_feed, monkeypatch):
    monkeypatch.setattr(feed_refresh, 'refresh_rss_feed', _raising_outcome)
    response = app_client.post(f'/api/v1/feeds/{SLUG}/refresh', json={}, headers=csrf(app_client))
    assert response.status_code == 502
    body = response.get_data(as_text=True)
    for leak in LEAKS:
        assert leak not in body


def test_all_feeds_refresh_redacts_the_outcome_error(app_client, fp_feed, monkeypatch):
    monkeypatch.setattr(feed_refresh, 'refresh_rss_feed', _raising_outcome)
    # The feed map is TTL-cached; an earlier test's snapshot would not hold this fixture feed.
    feed_refresh.invalidate_feed_cache()
    response = app_client.post('/api/v1/feeds/refresh', json={'force': True}, headers=csrf(app_client))
    assert response.status_code in (200, 207)
    outcome = response.get_json()['outcomes'].get(SLUG)
    assert outcome and outcome['error'] and not any(leak in outcome['error'] for leak in LEAKS)


@pytest.mark.parametrize('method,url,body', [
    ('put', '/api/v1/settings/rate-limit-hold', {'llmUsageUrl': 'http://169.254.169.254/usage?key=SECRET'}),
    ('put', '/api/v1/podping/nodes', {'nodes': ['not a url']}),
])
def test_400_bodies_carry_status(app_client, method, url, body):
    response = getattr(app_client, method)(url, json=body, headers=csrf(app_client))
    assert response.status_code == 400
    assert response.get_json()['status'] == 400
    assert 'SECRET' not in response.get_data(as_text=True)
