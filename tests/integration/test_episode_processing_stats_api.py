"""Integration tests for the episode processing-stats API surface (#519).

GET /feeds/<slug>/episodes/<id> exposes per-run processingRuns (with the
stats blob), rssDuration, and the lowAdYield comparison; GET /history rows
carry downloadedDuration pulled from the blob.
"""
import os
import sys
import tempfile
from decimal import Decimal

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='proc-stats-test-'))

from config import normalize_model_key  # noqa: E402

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
    'timings': {
        'download': 42.0,
        'transcription': 180.0,
        'differential': 3.5,
        'audio_analysis': 8.0,
        'detection': 120.0,
        'refine_validate': 4.0,
        'cut': 8.0,
        'verification': 20.0,
        'normalization': 0.0,
        'assets': 2.0,
        'finalize': 1.0,
        'ffmpeg': 31.0,
    },
    'thinking_notices': [{
        'pass': 'ad_detection_pass_1',
        'provider': 'openai-compatible',
        'model': 'test-model',
        'requested': 'none',
        'compatibility': 'required',
        'fallback': {
            'max_tokens': 4096,
            'temperature': 0.0,
            'reasoning_effort': None,
        },
    }],
}

STATS_API = {
    'mode': 'auto',
    'detectionSkipped': None,
    'verificationSkipped': None,
    'cueOnly': None,
    'transcriptionSkipped': None,
    'downloadedDuration': 3305.7,
    'transcriptSegments': 132,
    'windows': {'total': 7, 'failed': 0},
    'verificationWindows': None,
    'stageHits': {'fingerprint': 0, 'textPattern': 3, 'differential': 11, 'llm': 11},
    'detected': 12,
    'markers': {'cut': 6, 'held': 4, 'notCut': 5},
    'verificationAdsCut': 1,
    'secondsRemoved': 609.0,
    'timings': {
        'downloadSeconds': 42.0,
        'transcriptionSeconds': 180.0,
        'differentialSeconds': 3.5,
        'audioAnalysisSeconds': 8.0,
        'detectionSeconds': 120.0,
        'refineValidateSeconds': 4.0,
        'cutSeconds': 8.0,
        'verificationSeconds': 20.0,
        'normalizationSeconds': 0.0,
        'assetsSeconds': 2.0,
        'finalizeSeconds': 1.0,
        'ffmpegSeconds': 31.0,
    },
    'thinkingNotices': [{
        'pass': 'ad_detection_pass_1',
        'provider': 'openai-compatible',
        'model': 'test-model',
        'requested': 'none',
        'compatibility': 'required',
        'fallback': {
            'maxTokens': 4096,
            'temperature': 0.0,
            'reasoningEffort': None,
        },
    }],
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


def test_failed_run_preserves_partial_stage_timings(app_client, seeded):
    db, slug, podcast = seeded['db'], seeded['slug'], seeded['podcast']
    seeded['seed']('faced1234567', original=3305.7, new=None)
    partial = {
        'mode': 'auto',
        'timings': {
            'download': 42.0,
            'transcription': 180.0,
            'ffmpeg': 7.5,
        },
    }
    db.record_processing_history(
        podcast_id=podcast['id'], podcast_slug=slug, podcast_title='Proc',
        episode_id='faced1234567', episode_title='Failed', status='failed',
        error_message='cut failed', processing_stats=partial)

    _authed(app_client)
    response = app_client.get(f'/api/v1/feeds/{slug}/episodes/faced1234567')
    assert response.status_code == 200, response.get_json()
    data = response.get_json()
    assert 'processingRuns' in data, data
    run = data['processingRuns'][0]
    assert run['status'] == 'failed'
    assert run['errorMessage'] == 'cut failed'
    assert run['stats']['timings'] == {
        'downloadSeconds': 42.0,
        'transcriptionSeconds': 180.0,
        'differentialSeconds': None,
        'audioAnalysisSeconds': None,
        'detectionSeconds': None,
        'refineValidateSeconds': None,
        'cutSeconds': None,
        'verificationSeconds': None,
        'normalizationSeconds': None,
        'assetsSeconds': None,
        'finalizeSeconds': None,
        'ffmpegSeconds': 7.5,
    }


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


def test_low_ad_yield_suppressed_for_skip_detection_run(app_client, seeded):
    db, slug, podcast = seeded['db'], seeded['slug'], seeded['podcast']
    for i in range(3):
        seeded['seed'](f'aaa00000000{i}', original=3300, new=2700)
    seeded['seed']('fff000000001', original=2784, new=2784)
    db.record_processing_history(
        podcast_id=podcast['id'], podcast_slug=slug, podcast_title='Proc',
        episode_id='fff000000001', episode_title='Skip', status='completed',
        ads_detected=0,
        processing_stats={'mode': 'auto', 'detection_skipped': True})

    _authed(app_client)
    resp = app_client.get(f'/api/v1/feeds/{slug}/episodes/fff000000001')
    assert resp.get_json()['lowAdYield'] is None


def test_low_ad_yield_absent_for_normal_yield(app_client, seeded):
    slug = seeded['slug']
    for i in range(3):
        seeded['seed'](f'aaa00000000{i}', original=3300, new=2700)
    seeded['seed']('eee000000001', original=3300, new=2750)

    _authed(app_client)
    resp = app_client.get(f'/api/v1/feeds/{slug}/episodes/eee000000001')
    assert resp.get_json()['lowAdYield'] is None


def test_partial_detection_present_when_degraded(app_client, seeded):
    db, slug, podcast = seeded['db'], seeded['slug'], seeded['podcast']
    seeded['seed']('aaa000000009', original=1000, new=900)
    db.upsert_episode(slug, 'aaa000000009',
                      detection_degraded='Ad detection failed: Overloaded')
    # windowsFailed/windowsTotal come from the run that produced the served
    # audio (the latest completed processing_history row), same lookup
    # _low_ad_yield uses.
    db.record_processing_history(
        podcast_id=podcast['id'], podcast_slug=slug, podcast_title='Proc',
        episode_id='aaa000000009', episode_title='Degraded', status='completed',
        ads_detected=1, processing_stats=STATS_DB)

    _authed(app_client)
    resp = app_client.get(f'/api/v1/feeds/{slug}/episodes/aaa000000009')
    assert resp.get_json()['partialDetection'] == {
        'reason': 'Ad detection failed: Overloaded',
        'windowsFailed': 0,
        'windowsTotal': 7,
    }


def test_partial_detection_window_counts_null_without_run_stats(app_client, seeded):
    db, slug = seeded['db'], seeded['slug']
    seeded['seed']('ccc000000009', original=1000, new=900)
    db.upsert_episode(slug, 'ccc000000009',
                      detection_degraded='Ad detection failed: Overloaded')

    _authed(app_client)
    resp = app_client.get(f'/api/v1/feeds/{slug}/episodes/ccc000000009')
    assert resp.get_json()['partialDetection'] == {
        'reason': 'Ad detection failed: Overloaded',
        'windowsFailed': None,
        'windowsTotal': None,
    }


def test_partial_detection_absent_when_not_degraded(app_client, seeded):
    slug = seeded['slug']
    seeded['seed']('bbb000000009', original=1000, new=900)

    _authed(app_client)
    resp = app_client.get(f'/api/v1/feeds/{slug}/episodes/bbb000000009')
    assert resp.get_json()['partialDetection'] is None


def test_incomplete_coverage_reports_lost_windows_per_pass(app_client, seeded):
    db, slug, podcast = seeded['db'], seeded['slug'], seeded['podcast']
    seeded['seed']('a1b2c3d4e5f6', original=1000, new=900)
    db.record_processing_history(
        podcast_id=podcast['id'], podcast_slug=slug, podcast_title='Proc',
        episode_id='a1b2c3d4e5f6', episode_title='Gaps', status='completed',
        ads_detected=4,
        processing_stats={**STATS_DB,
                          'windows': {'total': 13, 'failed': 3},
                          'verification_windows': {'total': 12, 'failed': 1}})

    _authed(app_client)
    data = app_client.get(f'/api/v1/feeds/{slug}/episodes/a1b2c3d4e5f6').get_json()
    assert data['incompleteCoverage'] == {
        'detection': {'failed': 3, 'total': 13},
        'verification': {'failed': 1, 'total': 12},
    }
    # Reported without detection_degraded: the run completed on its own terms.
    assert data['partialDetection'] is None


def test_incomplete_coverage_absent_for_a_clean_run(app_client, seeded):
    db, slug, podcast = seeded['db'], seeded['slug'], seeded['podcast']
    seeded['seed']('b1b2c3d4e5f6', original=1000, new=900)
    db.record_processing_history(
        podcast_id=podcast['id'], podcast_slug=slug, podcast_title='Proc',
        episode_id='b1b2c3d4e5f6', episode_title='Clean', status='completed',
        ads_detected=4,
        processing_stats={**STATS_DB,
                          'verification_windows': {'total': 12, 'failed': 0}})

    _authed(app_client)
    data = app_client.get(f'/api/v1/feeds/{slug}/episodes/b1b2c3d4e5f6').get_json()
    assert data['incompleteCoverage'] is None
    assert data['processingRuns'][0]['stats']['verificationWindows'] == {
        'total': 12, 'failed': 0}


def test_incomplete_coverage_alongside_a_degraded_run(app_client, seeded):
    db, slug, podcast = seeded['db'], seeded['slug'], seeded['podcast']
    seeded['seed']('c1b2c3d4e5f6', original=1000, new=900)
    db.upsert_episode(slug, 'c1b2c3d4e5f6',
                      detection_degraded='Ad detection failed: Overloaded')
    db.record_processing_history(
        podcast_id=podcast['id'], podcast_slug=slug, podcast_title='Proc',
        episode_id='c1b2c3d4e5f6', episode_title='Degraded', status='completed',
        ads_detected=1,
        processing_stats={**STATS_DB, 'windows': {'total': 13, 'failed': 13}})

    _authed(app_client)
    data = app_client.get(f'/api/v1/feeds/{slug}/episodes/c1b2c3d4e5f6').get_json()
    assert data['partialDetection'] == {
        'reason': 'Ad detection failed: Overloaded',
        'windowsFailed': 13,
        'windowsTotal': 13,
    }
    assert data['incompleteCoverage'] == {'detection': {'failed': 13, 'total': 13}}


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


def test_history_rows_carry_app_version(app_client, seeded):
    from version import __version__

    db, slug, podcast = seeded['db'], seeded['slug'], seeded['podcast']
    db.record_processing_history(
        podcast_id=podcast['id'], podcast_slug=slug, podcast_title='Proc',
        episode_id='ver123def456', episode_title='One', status='completed',
        ads_detected=1)
    conn = db.get_connection()
    conn.execute(
        "UPDATE processing_history SET app_version = NULL WHERE episode_id = ?",
        ('ver123def456',),
    )
    conn.commit()
    db.record_processing_history(
        podcast_id=podcast['id'], podcast_slug=slug, podcast_title='Proc',
        episode_id='ver123def456', episode_title='One', status='completed',
        ads_detected=2)

    _authed(app_client)
    resp = app_client.get(f'/api/v1/history?podcast_slug={slug}')
    assert resp.status_code == 200
    entries = resp.get_json()['history']
    versions = {e['reprocessNumber']: e['appVersion'] for e in entries}
    assert versions[1] is None
    assert versions[2] == __version__


def test_processing_runs_expose_phase_breakdown_and_episode_spend(app_client, seeded):
    db, slug, podcast = seeded['db'], seeded['slug'], seeded['podcast']
    seeded['seed']('e1e2e3e4e5e6', original=1000, new=900)

    # Run 1: legacy, no run_id/ledger rows, keeps its own recorded total.
    db.record_processing_history(
        podcast_id=podcast['id'], podcast_slug=slug, podcast_title='Proc',
        episode_id='e1e2e3e4e5e6', episode_title='Legacy', status='completed',
        ads_detected=1, input_tokens=100, output_tokens=50, llm_cost=0.01)

    # Run 2: ledger-backed detection(anthropic) + review(ollama).
    for model_id, in_cost, out_cost in (
            ('claude-detect-e2e', 3.0, 15.0), ('ollama-review-e2e', 1.0, 2.0)):
        db.upsert_fetched_pricing([{
            'match_key': normalize_model_key(model_id),
            'raw_model_id': model_id,
            'display_name': model_id,
            'input_cost_per_mtok': in_cost,
            'output_cost_per_mtok': out_cost,
        }], source='litellm')

    a1 = db.begin_llm_attempt(
        run_id='run-e2e-1', podcast_id=podcast['id'], episode_id='e1e2e3e4e5e6',
        phase_key='detect', invoking_pass=1, provider_key='anthropic',
        configured_model='claude-detect-e2e')
    db.finalize_llm_attempt(a1, state='success', returned_model='claude-detect-e2e',
                            input_tokens=1000, output_tokens=200)
    a2 = db.begin_llm_attempt(
        run_id='run-e2e-1', podcast_id=podcast['id'], episode_id='e1e2e3e4e5e6',
        phase_key='review', invoking_pass=1, provider_key='ollama',
        configured_model='ollama-review-e2e')
    db.finalize_llm_attempt(a2, state='success', returned_model='ollama-review-e2e',
                            input_tokens=500, output_tokens=100)

    run_subtotal = db.get_run_usage_totals('run-e2e-1')
    db.record_processing_history(
        podcast_id=podcast['id'], podcast_slug=slug, podcast_title='Proc',
        episode_id='e1e2e3e4e5e6', episode_title='Ledgered', status='completed',
        ads_detected=2, input_tokens=run_subtotal['input_tokens'],
        output_tokens=run_subtotal['output_tokens'],
        llm_cost=float(run_subtotal['cost_usd']), run_id='run-e2e-1')

    _authed(app_client)
    resp = app_client.get(f'/api/v1/feeds/{slug}/episodes/e1e2e3e4e5e6')
    assert resp.status_code == 200
    data = resp.get_json()

    runs = data['processingRuns']
    assert runs[0]['breakdownAvailable'] is False
    assert runs[0]['phases'] == []
    assert runs[0]['inputTokens'] == 100
    assert runs[0]['llmCost'] == 0.01

    assert runs[1]['breakdownAvailable'] is True
    phases = {p['phaseKey']: p for p in runs[1]['phases']}
    assert len(phases) == 2
    assert phases['detect']['provider'] == 'anthropic'
    assert phases['detect']['configuredModel'] == 'claude-detect-e2e'
    assert phases['detect']['inputTokens'] == 1000
    assert phases['detect']['outputTokens'] == 200
    assert phases['review']['provider'] == 'ollama'
    assert phases['review']['configuredModel'] == 'ollama-review-e2e'
    phase_cost_sum = sum((Decimal(p['costUsd']) for p in phases.values()), Decimal('0'))
    assert phase_cost_sum == Decimal(run_subtotal['cost_usd'])

    assert data['activeRunSpend'] is None
    assert data['latestRunSpend']['breakdownAvailable'] is True
    assert data['latestRunSpend']['runId'] == runs[1]['runId']
    assert data['latestRunSpend']['inputTokens'] == 1500
    assert data['latestRunSpend']['hasUnknownCost'] is False
    assert Decimal(data['latestRunSpend']['costUsd']) == Decimal(run_subtotal['cost_usd'])

    assert data['cumulativeSpend']['inputTokens'] == 1500
    assert Decimal(data['cumulativeSpend']['costUsd']) == Decimal(run_subtotal['cost_usd'])
    assert data['cumulativeSpend']['hasUnknownCost'] is False


TRANSCRIPTION_DB = {
    'outcome': 'success',
    'batch_size': 8,
    'retry_count': 1,
    'retry_succeeded': True,
    'device': 'cuda',
    'gpu_device_name': 'Test GPU',
    'model': 'large-v3',
    'error': None,
}


def test_run_stats_expose_the_transcription_block(app_client, seeded):
    db, slug, podcast = seeded['db'], seeded['slug'], seeded['podcast']
    seeded['seed']('ab12cd34ef56')
    db.record_processing_history(
        podcast_id=podcast['id'], podcast_slug=slug, podcast_title='Proc',
        episode_id='ab12cd34ef56', episode_title='One', status='completed',
        ads_detected=1,
        processing_stats={**STATS_DB, 'transcription': TRANSCRIPTION_DB})

    _authed(app_client)
    data = app_client.get(f'/api/v1/feeds/{slug}/episodes/ab12cd34ef56').get_json()
    assert data['processingRuns'][0]['stats']['transcription'] == {
        'outcome': 'success',
        'batchSize': 8,
        'retryCount': 1,
        'retrySucceeded': True,
        'device': 'cuda',
        'gpuDeviceName': 'Test GPU',
        'model': 'large-v3',
        'error': None,
    }


def test_run_without_transcription_stats_omits_the_block(app_client, seeded):
    db, slug, podcast = seeded['db'], seeded['slug'], seeded['podcast']
    seeded['seed']('ba21dc43fe65')
    db.record_processing_history(
        podcast_id=podcast['id'], podcast_slug=slug, podcast_title='Proc',
        episode_id='ba21dc43fe65', episode_title='One', status='completed',
        ads_detected=1, processing_stats=STATS_DB)

    _authed(app_client)
    data = app_client.get(f'/api/v1/feeds/{slug}/episodes/ba21dc43fe65').get_json()
    assert 'transcription' not in data['processingRuns'][0]['stats']


def test_latest_run_spend_uses_the_latest_attempt_not_the_latest_success(
        app_client, seeded):
    db, slug, podcast = seeded['db'], seeded['slug'], seeded['podcast']
    seeded['seed']('cd12ef34ab56')
    db.record_processing_history(
        podcast_id=podcast['id'], podcast_slug=slug, podcast_title='Proc',
        episode_id='cd12ef34ab56', episode_title='Ok', status='completed',
        ads_detected=1, input_tokens=100, output_tokens=50, llm_cost=0.01)
    db.record_processing_history(
        podcast_id=podcast['id'], podcast_slug=slug, podcast_title='Proc',
        episode_id='cd12ef34ab56', episode_title='Failed', status='failed',
        ads_detected=0)

    _authed(app_client)
    data = app_client.get(f'/api/v1/feeds/{slug}/episodes/cd12ef34ab56').get_json()
    # The failed run spent nothing; reporting the earlier success as the
    # latest run overstated what the last attempt cost.
    assert data['latestRunSpend']['inputTokens'] == 0
    assert Decimal(data['latestRunSpend']['costUsd']) == Decimal('0')
    assert data['activeRunSpend'] is None


def test_active_run_spend_reads_the_live_ledger(app_client, seeded):
    db, slug, podcast = seeded['db'], seeded['slug'], seeded['podcast']
    seeded['seed']('de12fa34bc56', status='processing')
    conn = db.get_connection()
    conn.execute(
        "INSERT INTO processing_runs (run_id, podcast_id, episode_id, owner_pid, state) "
        "VALUES ('run-active-1', ?, ?, ?, 'running')",
        (podcast['id'], 'de12fa34bc56', os.getpid()),
    )
    conn.commit()
    attempt = db.begin_llm_attempt(
        run_id='run-active-1', podcast_id=podcast['id'], episode_id='de12fa34bc56',
        phase_key='detect', invoking_pass=1, provider_key='anthropic',
        configured_model='claude-active')
    db.finalize_llm_attempt(attempt, state='success', returned_model='claude-active',
                            input_tokens=700, output_tokens=100)
    try:
        _authed(app_client)
        data = app_client.get(f'/api/v1/feeds/{slug}/episodes/de12fa34bc56').get_json()
        assert data['jobState'] == 'processing'
        assert data['activeRunSpend']['runId'] == 'run-active-1'
        assert data['activeRunSpend']['inputTokens'] == 700
        assert data['activeRunSpend']['outputTokens'] == 100
        assert data['activeRunSpend']['hasUnknownCost'] is True
        # No history row yet: the finished-run field must not borrow from it.
        assert data['latestRunSpend'] is None
    finally:
        conn.execute("DELETE FROM processing_runs WHERE run_id = 'run-active-1'")
        conn.commit()
