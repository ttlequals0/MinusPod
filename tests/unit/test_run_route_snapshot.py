"""Tests for the per-run route snapshot on RunContext and its persisted column."""
import json
from unittest.mock import MagicMock, patch

import pytest

from tests.app_bootstrap import bootstrap

bootstrap('run_route_snapshot_test_')

import run_context
from database import Database
from main_app import processing
from llm_client import ProviderAccountChangedError, ProviderRateLimitedError
from utils.errors import ServiceUnavailableError
from cancel import ProcessingCancelled


def test_route_snapshot_defaults_none_and_sets():
    ctx = run_context.RunContext('example-podcast', 'a1b2c3d4e5f6', run_id='r1')
    assert ctx.route_snapshot is None
    snap = {'detection': {'provider_key': 'anthropic', 'configured_model': 'claude-x'},
            'review': {'provider_key': 'ollama', 'configured_model': 'local-y'}}
    ctx.set_route_snapshot(snap)
    assert ctx.route_snapshot == snap


def test_route_snapshot_rejects_secret_keys():
    ctx = run_context.RunContext('example-podcast', 'a1b2c3d4e5f6', run_id='r1')
    import pytest
    with pytest.raises(ValueError):
        ctx.set_route_snapshot({'detection': {'api_key': 'sk-secret'}})


def test_route_snapshot_rejects_base_url_with_embedded_credentials():
    ctx = run_context.RunContext('example-podcast', 'a1b2c3d4e5f6', run_id='r1')
    import pytest
    with pytest.raises(ValueError):
        ctx.set_route_snapshot({'detection': {
            'provider_key': 'openai-compatible',
            'base_url': 'https://user:pass@example.com/v1'}})


def test_processing_runs_route_snapshot_column_on_fresh_db(tmp_path):
    Database._instance = None
    db = Database(data_dir=str(tmp_path))
    try:
        cols = {r['name'] for r in
                db.get_connection().execute("PRAGMA table_info(processing_runs)")}
        assert 'route_snapshot_json' in cols
    finally:
        Database._instance = None


def test_processing_runs_route_snapshot_column_on_existing_db(tmp_path):
    Database._instance = None
    db = Database(data_dir=str(tmp_path))
    try:
        conn = db.get_connection()
        conn.execute("ALTER TABLE processing_runs RENAME TO processing_runs_old")
        conn.execute("""CREATE TABLE processing_runs (
            run_id TEXT PRIMARY KEY,
            podcast_id INTEGER NOT NULL REFERENCES podcasts(id) ON DELETE CASCADE,
            episode_id TEXT NOT NULL,
            owner_pid INTEGER NOT NULL,
            owner_pid_start REAL,
            state TEXT NOT NULL CHECK(state IN ('running', 'cancel_requested', 'finished', 'interrupted')),
            cancel_requested_at TEXT,
            started_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
            heartbeat_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
            finished_at TEXT
        )""")
        conn.execute("DROP TABLE processing_runs_old")
        conn.commit()
        cols = {r['name'] for r in conn.execute("PRAGMA table_info(processing_runs)")}
        assert 'route_snapshot_json' not in cols

        db._run_schema_migrations()

        cols = {r['name'] for r in conn.execute("PRAGMA table_info(processing_runs)")}
        assert 'route_snapshot_json' in cols
    finally:
        Database._instance = None


def _reviewer():
    from unittest.mock import MagicMock
    from ad_reviewer import AdReviewer
    return AdReviewer(MagicMock())


def test_reviewer_same_as_pass_pass1_inherits_detection_route():
    reviewer = _reviewer()
    ctx = run_context.begin('example-podcast', 'a1b2c3d4e5f6', run_id='r-rev1')
    try:
        ctx.set_route_snapshot({
            'detection': {'provider_key': 'openai-compatible', 'configured_model': 'm-detect',
                          'base_url': 'https://secondary.example/v1', 'credential_slot': 'secondary'},
            'verification': {'provider_key': 'anthropic', 'configured_model': 'm-verify',
                             'base_url': None, 'credential_slot': 'primary'},
            'review': {'provider_key': 'openai-compatible', 'configured_model': 'm-detect',
                       'base_url': 'https://secondary.example/v1', 'credential_slot': 'secondary',
                       'gate': {'review_provider': 'same_as_pass', 'review_model': 'same_as_pass'}},
        })
        route = reviewer._resolve_route('openai-compatible', 'm-detect', pass_num=1)
        assert route.provider_key == 'openai-compatible'
        assert route.base_url == 'https://secondary.example/v1'
        assert route.credential_slot == 'secondary'
    finally:
        run_context.end(ctx)


def test_reviewer_same_as_pass_pass2_inherits_verification_route():
    reviewer = _reviewer()
    ctx = run_context.begin('example-podcast', 'a1b2c3d4e5f6', run_id='r-rev2')
    try:
        ctx.set_route_snapshot({
            'detection': {'provider_key': 'openai-compatible', 'configured_model': 'm-detect',
                          'base_url': 'https://secondary.example/v1', 'credential_slot': 'secondary'},
            'verification': {'provider_key': 'anthropic', 'configured_model': 'm-verify',
                             'base_url': None, 'credential_slot': 'primary'},
            'review': {'provider_key': 'openai-compatible', 'configured_model': 'm-detect',
                       'base_url': 'https://secondary.example/v1', 'credential_slot': 'secondary',
                       'gate': {'review_provider': 'same_as_pass', 'review_model': 'same_as_pass'}},
        })
        route = reviewer._resolve_route('anthropic', 'm-verify', pass_num=2)
        assert route.provider_key == 'anthropic'
        assert route.credential_slot == 'primary'
    finally:
        run_context.end(ctx)


def test_reviewer_explicit_slot_uses_frozen_review_route():
    reviewer = _reviewer()
    ctx = run_context.begin('example-podcast', 'a1b2c3d4e5f6', run_id='r-rev3')
    try:
        ctx.set_route_snapshot({
            'detection': {'provider_key': 'anthropic', 'configured_model': 'm-detect',
                          'base_url': None, 'credential_slot': 'primary'},
            'review': {'provider_key': 'openai-compatible', 'configured_model': 'm-review',
                       'base_url': 'https://secondary.example/v1', 'credential_slot': 'secondary',
                       'gate': {'review_provider': 'secondary', 'review_model': 'm-review'}},
        })
        route = reviewer._resolve_route('anthropic', 'm-detect', pass_num=1)
        assert route.provider_key == 'openai-compatible'
        assert route.model_id == 'm-review'
        assert route.credential_slot == 'secondary'
    finally:
        run_context.end(ctx)


def test_legacy_standby_snapshot_requeues_without_spending_retry_budget(monkeypatch):
    monkeypatch.setattr('transcriber.WhisperModelSingleton.unload_model', lambda: None)
    monkeypatch.setattr('utils.gpu.clear_gpu_memory', lambda: None)
    db = processing.db
    slug, episode_id, run_id = 'legacy-route', 'a1b2c3d4e5f6', 'legacy-route-run'
    db.create_podcast(slug, 'https://example.com/feed.xml', title='Route Test')
    db.upsert_episode(slug, episode_id, title='Episode', status='processing',
                      original_url='https://example.com/episode.mp3', retry_count=2)
    podcast_id = db.get_podcast_by_slug(slug)['id']
    legacy = {'detection': {'provider_key': 'openai-compatible',
                           'configured_model': 'standby-model', 'credential_slot': 'failover',
                           'failover_from': 'primary'}}
    conn = db.get_connection()
    conn.execute("INSERT INTO processing_runs (run_id, podcast_id, episode_id, owner_pid, "
                 "state, route_snapshot_json) VALUES (?, ?, ?, 1, 'running', ?)",
                 (run_id, podcast_id, episode_id, json.dumps(legacy)))
    conn.commit()
    ctx = run_context.begin(slug, episode_id, run_id=run_id)
    try:
        loaded = processing._resolve_or_load_route_snapshot(run_id)
        with pytest.raises(ProviderAccountChangedError) as caught:
            processing._assert_route_snapshot_current(loaded)
        with patch.object(processing, '_require_publication_owner'), \
                patch.object(processing, '_publish_status'):
            processing._handle_processing_failure(
                slug, episode_id, 'Episode', 'Route Test', db.get_episode(slug, episode_id),
                caught.value, 0.0)
        assert processing._load_route_snapshot(run_id) is None
        episode = db.get_episode(slug, episode_id)
        assert episode['status'] == 'pending'
        assert episode['retry_count'] == 2
        original = {'detection': {'provider_key': 'anthropic', 'configured_model': 'primary-model',
                                 'credential_slot': 'primary'},
                    'verification': {'provider_key': 'openai-compatible',
                                     'configured_model': 'secondary-model', 'credential_slot': 'secondary'}}
        with patch.object(processing, '_resolve_route_snapshot', return_value=original):
            assert processing._resolve_or_load_route_snapshot(run_id) == original
        assert processing._load_route_snapshot(run_id) == original
    finally:
        run_context.end(ctx)
        db.delete_podcast(slug)


@pytest.mark.parametrize('failure', ['offline', 'hold', 'account'])
def test_early_deferral_persists_actual_standby_usage(temp_db, monkeypatch, failure):
    temp_db.create_podcast('usage-history', 'https://example.com/feed.xml', 'Route Test')
    temp_db.upsert_episode('usage-history', 'episode', status='processing', retry_count=2,
                           original_url='https://example.com/episode.mp3')
    temp_db.set_setting('offline_queue_enabled', 'true')
    monkeypatch.setattr(processing, 'db', temp_db)
    monkeypatch.setattr(processing, '_require_publication_owner', lambda *a: None)
    monkeypatch.setattr(processing, '_publish_status', lambda *a: None)
    monkeypatch.setattr(processing, '_finalize_run_log', lambda *a: None)
    monkeypatch.setattr(processing, 'clear_route_snapshot', lambda *a: None)
    monkeypatch.setattr('transcriber.WhisperModelSingleton.unload_model', lambda: None)
    monkeypatch.setattr('utils.gpu.clear_gpu_memory', lambda: None)
    monkeypatch.setattr(processing, 'hold_queue_for_provider_limit', lambda *a, **k: '2999-01-01T00:00:00Z')
    ctx = run_context.begin('usage-history', 'episode', run_id='usage-history-run')
    ctx.set_route_snapshot({'detection': {'credential_slot': 'primary'}})
    ctx.note_llm_failover('detection', 1)
    errors = {'offline': ServiceUnavailableError('llm', 'standby down'),
              'hold': ProviderRateLimitedError('standby429', 300, credential_slot='failover'),
              'account': ProviderAccountChangedError('account changed')}
    try:
        processing._handle_processing_failure(
            'usage-history', 'episode', 'Episode', 'Route Test',
            temp_db.get_episode('usage-history', 'episode'), errors[failure], 0.0)
        rows = temp_db.get_connection().execute('SELECT * FROM processing_history').fetchall()
        assert len(rows) == 1
        assert json.loads(rows[0]['processing_stats_json'])['failover'] == {
            'llm': ['primary'], 'whisper': False}
        assert temp_db.get_episode('usage-history', 'episode')['retry_count'] == 2
        processing._record_standby_partial_history(
            'usage-history', 'episode', 'Episode', 'Route Test', errors[failure], 0.0)
        assert temp_db.get_connection().execute('SELECT COUNT(*) FROM processing_history').fetchone()[0] == 1
    finally:
        run_context.end(ctx)


@pytest.mark.parametrize('dispatched', [False, True])
def test_cancellation_records_history_only_after_actual_standby(temp_db, monkeypatch, dispatched):
    temp_db.create_podcast('cancel-usage', 'https://example.com/feed.xml', 'Route Test')
    temp_db.upsert_episode('cancel-usage', 'episode', status='processing', retry_count=2)
    monkeypatch.setattr(processing, 'db', temp_db)
    monkeypatch.setattr(processing, '_start_run_log', lambda *a: None)
    monkeypatch.setattr(processing, '_end_run_log', lambda *a: None)
    monkeypatch.setattr(processing, '_finalize_run_log', lambda *a: None)
    monkeypatch.setattr(processing, 'ProcessingQueue', MagicMock())
    monkeypatch.setattr(processing, 'status_service', MagicMock())
    monkeypatch.setattr(processing, '_cleanup_cancelled_processing_output', lambda *a, **k: (True, False))
    def cancel(*args, **kwargs):
        ctx = run_context.current()
        ctx.set_route_snapshot({'detection': {'credential_slot': 'primary'}})
        if dispatched:
            ctx.note_llm_failover('detection', 1)
        raise ProcessingCancelled('cancelled')
    monkeypatch.setattr(processing, 'process_episode', cancel)
    processing._process_episode_background(
        'cancel-usage', 'episode', 'https://example.com/episode.mp3',
        'Episode', 'Route Test', '', None, run_id='cancel-usage-run')
    rows = temp_db.get_connection().execute('SELECT * FROM processing_history').fetchall()
    assert len(rows) == int(dispatched)
    if dispatched:
        assert rows[0]['error_message'] == processing.CANCELED_ERROR_MESSAGE
        assert json.loads(rows[0]['processing_stats_json'])['failover']['llm'] == ['primary']
    assert temp_db.get_episode('cancel-usage', 'episode')['status'] == 'pending'
