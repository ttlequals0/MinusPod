"""Integration tests for the episode processing-stats API surface (#519).

GET /feeds/<slug>/episodes/<id> exposes per-run processingRuns (with the
stats blob), rssDuration, and the lowAdYield comparison; GET /history rows
carry downloadedDuration pulled from the blob.
"""
import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='proc-stats-test-'))

# Stored (pipeline) form: snake_case, renamed to API casing by the endpoint.
STATS_DB = {
    'mode': 'auto',
    'downloaded_duration': 3305.7,
    'transcript_segments': 132,
    'windows': {'total': 7, 'failed': 0},
    'stage_hits': {'fingerprint': 0, 'text_pattern': 3, 'differential': 11, 'llm': 11},
    'detected': 12,
    'markers': {'cut': 6, 'held': 4, 'not_cut': 5},
    'verification_ads_cut': 1,
    'seconds_removed': 609.0,
}

STATS_API = {
    'mode': 'auto',
    'downloadedDuration': 3305.7,
    'transcriptSegments': 132,
    'windows': {'total': 7, 'failed': 0},
    'stageHits': {'fingerprint': 0, 'textPattern': 3, 'differential': 11, 'llm': 11},
    'detected': 12,
    'markers': {'cut': 6, 'held': 4, 'notCut': 5},
    'verificationAdsCut': 1,
    'secondsRemoved': 609.0,
}


def _authed(client):
    with client.session_transaction() as sess:
        sess['authenticated'] = True


@pytest.fixture
def seeded(app_client):
    from api import get_database
    db = get_database()
    slug = 'proc-stats-feed'
    db.create_podcast(slug, 'https://example.com/feed.xml', 'Proc Stats Test')
    podcast = db.get_podcast_by_slug(slug)

    def seed_episode(ep_id, original=None, new=None, status='processed'):
        db.upsert_episode(slug, ep_id,
                          original_url=f'https://example.com/{ep_id}.mp3',
                          title=ep_id, status=status,
                          original_duration=original, new_duration=new,
                          processed_at='2026-07-01T00:00:00Z')

    yield {'slug': slug, 'db': db, 'podcast': podcast, 'seed': seed_episode}
    db.delete_podcast(slug)


def test_episode_exposes_processing_runs_and_rss_duration(app_client, seeded):
    db, slug, podcast = seeded['db'], seeded['slug'], seeded['podcast']
    seeded['seed']('abc123def456', original=3305.7, new=2696.7)
    conn = db.get_connection()
    conn.execute("UPDATE episodes SET rss_duration = 3300.0 WHERE episode_id = ?",
                 ('abc123def456',))
    conn.commit()
    db.record_processing_history(
        podcast_id=podcast['id'], podcast_slug=slug, podcast_title='Proc',
        episode_id='abc123def456', episode_title='One', status='completed',
        ads_detected=1, input_tokens=100, output_tokens=50, llm_cost=0.01)
    db.record_processing_history(
        podcast_id=podcast['id'], podcast_slug=slug, podcast_title='Proc',
        episode_id='abc123def456', episode_title='One', status='completed',
        ads_detected=6, processing_stats=STATS_DB)

    _authed(app_client)
    resp = app_client.get(f'/api/v1/feeds/{slug}/episodes/abc123def456')
    assert resp.status_code == 200
    data = resp.get_json()

    assert data['rssDuration'] == 3300.0
    runs = data['processingRuns']
    assert [r['runNumber'] for r in runs] == [1, 2]
    assert runs[0]['stats'] is None
    assert runs[1]['stats'] == STATS_API
    assert runs[1]['adsDetected'] == 6


def test_low_ad_yield_flags_light_copy(app_client, seeded):
    slug = seeded['slug']
    for i in range(3):
        seeded['seed'](f'aaa00000000{i}', original=3300, new=3300 - 600)
    seeded['seed']('ddd000000001', original=2784, new=2741)

    _authed(app_client)
    resp = app_client.get(f'/api/v1/feeds/{slug}/episodes/ddd000000001')
    data = resp.get_json()
    assert data['lowAdYield'] == {
        'removedSeconds': 43.0,
        'feedAverageSeconds': 600.0,
        'sampleSize': 3,
    }


def test_low_ad_yield_needs_enough_samples(app_client, seeded):
    slug = seeded['slug']
    for i in range(2):
        seeded['seed'](f'aaa00000000{i}', original=3300, new=2700)
    seeded['seed']('ddd000000001', original=2784, new=2741)

    _authed(app_client)
    resp = app_client.get(f'/api/v1/feeds/{slug}/episodes/ddd000000001')
    assert resp.get_json()['lowAdYield'] is None


def test_low_ad_yield_absent_for_normal_yield(app_client, seeded):
    slug = seeded['slug']
    for i in range(3):
        seeded['seed'](f'aaa00000000{i}', original=3300, new=2700)
    seeded['seed']('eee000000001', original=3300, new=2750)

    _authed(app_client)
    resp = app_client.get(f'/api/v1/feeds/{slug}/episodes/eee000000001')
    assert resp.get_json()['lowAdYield'] is None


def test_history_rows_carry_downloaded_duration(app_client, seeded):
    db, slug, podcast = seeded['db'], seeded['slug'], seeded['podcast']
    db.record_processing_history(
        podcast_id=podcast['id'], podcast_slug=slug, podcast_title='Proc',
        episode_id='abc123def456', episode_title='One', status='completed',
        ads_detected=6, processing_stats=STATS_DB)
    db.record_processing_history(
        podcast_id=podcast['id'], podcast_slug=slug, podcast_title='Proc',
        episode_id='abc123def456', episode_title='One', status='completed',
        ads_detected=1)

    _authed(app_client)
    resp = app_client.get(f'/api/v1/history?podcast_slug={slug}')
    assert resp.status_code == 200
    entries = resp.get_json()['history']
    durations = {e['reprocessNumber']: e['downloadedDuration'] for e in entries}
    assert durations[1] == 3305.7
    assert durations[2] is None
