"""Provider-account lifecycle API: the affected-runs preflight and what a
save does to runs frozen against the account it replaces (F01).
"""
import json
from unittest.mock import patch

import pytest

from tests.app_bootstrap import bootstrap

bootstrap('provider_account_api_test_', passphrase='provider-account-test-pass')

from main_app import app  # noqa: E402
from api import get_database  # noqa: E402
from llm_client import invalidate_provider_cache  # noqa: E402
from config import ModelNotConfiguredError  # noqa: E402
from llm_route import account_identity, resolve_route  # noqa: E402

OLD_BASE = 'https://old.example/v1'
NEW_BASE = 'https://new.example/v1'


@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c


@pytest.fixture
def openai_primary(monkeypatch):
    """Primary slot on openai-compatible at OLD_BASE, with one active run
    frozen against that account."""
    for var in ('OPENAI_BASE_URL', 'OPENAI_API_KEY', 'LLM_PROVIDER'):
        monkeypatch.delenv(var, raising=False)
    db = get_database()
    saved = {key: db.get_setting(key)
             for key in ('llm_provider', 'openai_base_url', 'openai_api_key')}
    db.set_setting('llm_provider', 'openai-compatible', is_default=False)
    db.set_setting('openai_base_url', OLD_BASE, is_default=False)
    invalidate_provider_cache()
    conn = db.get_connection()
    conn.execute("INSERT OR IGNORE INTO podcasts (slug, title, source_url) "
                 "VALUES ('account-change-feed', 'The Daily Tech Show', 'https://example.com/f')")
    podcast_id = conn.execute(
        "SELECT id FROM podcasts WHERE slug = 'account-change-feed'").fetchone()['id']
    snapshot = {'detection': {
        'provider_key': 'openai-compatible', 'configured_model': 'm',
        'base_url': OLD_BASE, 'credential_slot': 'primary',
        'account_id': account_identity('openai-compatible', OLD_BASE)}}
    conn.execute(
        "INSERT INTO processing_runs (run_id, podcast_id, episode_id, owner_pid, "
        "state, route_snapshot_json) VALUES ('run-1', ?, 'a1b2c3d4e5f6', 1, "
        "'running', ?)", (podcast_id, json.dumps(snapshot)))
    conn.commit()
    yield db
    conn.execute("DELETE FROM processing_runs WHERE run_id = 'run-1'")
    conn.commit()
    db.delete_podcast('account-change-feed')
    for key, value in saved.items():
        if value is None:
            db.clear_setting(key)
        else:
            db.set_setting(key, value, is_default=False)
    invalidate_provider_cache()


@pytest.fixture
def primary_stage_models(openai_primary):
    db = openai_primary
    keys = (
        'claude_model', 'verification_model', 'chapters_model', 'review_model',
        'pattern_cleanup_model', 'detection_provider', 'verification_provider',
        'chapters_provider', 'review_provider', 'pattern_cleanup_provider',
        'secondary_provider_enabled', 'secondary_provider', 'secondary_provider_base_url',
    )
    conn = db.get_connection()
    saved = {
        key: conn.execute(
            'SELECT value, is_default FROM settings WHERE key = ?', (key,)).fetchone()
        for key in keys
    }
    for key, value in {
        'claude_model': 'detection-model',
        'verification_model': 'verification-model',
        'chapters_model': 'chapters-model',
        'review_model': 'review-model',
        'pattern_cleanup_model': 'cleanup-model',
        'detection_provider': 'primary',
        'verification_provider': 'secondary',
        'chapters_provider': 'primary',
        'review_provider': 'primary',
        'pattern_cleanup_provider': 'primary',
        'secondary_provider_enabled': 'true',
        'secondary_provider': 'openai-compatible',
        'secondary_provider_base_url': 'https://secondary.example/v1',
    }.items():
        db.set_setting(key, value, is_default=False)
    yield db
    for key, row in saved.items():
        if row is None:
            db.clear_setting(key)
        else:
            db.set_setting(key, row['value'], is_default=bool(row['is_default']))


class TestAffectedRunsPreflight:
    def test_lists_runs_bound_to_the_slots_current_account(self, client, openai_primary):
        r = client.get('/api/v1/settings/providers/primary/affected-runs')
        assert r.status_code == 200
        body = r.get_json()
        assert body['slot'] == 'primary'
        assert body['accountId'] == account_identity('openai-compatible', OLD_BASE)
        assert body['affectedRuns']['count'] == 1
        run = body['affectedRuns']['runs'][0]
        assert run['runId'] == 'run-1'
        assert run['slug'] == 'account-change-feed'
        assert run['episodeId'] == 'a1b2c3d4e5f6'
        assert run['state'] == 'running'
        assert run['phases'] == ['detection']

    def test_other_slot_does_not_claim_the_run(self, client, openai_primary):
        r = client.get('/api/v1/settings/providers/secondary/affected-runs')
        assert r.status_code == 200
        assert r.get_json()['affectedRuns']['count'] == 0

    def test_unknown_slot_is_404(self, client, openai_primary):
        assert client.get(
            '/api/v1/settings/providers/tertiary/affected-runs').status_code == 404


def _persisted_snapshot(db, run_id='run-1'):
    row = db.get_connection().execute(
        "SELECT route_snapshot_json FROM processing_runs WHERE run_id = ?",
        (run_id,)).fetchone()
    return row['route_snapshot_json']


class TestSaveReportsAndResolvesAffectedRuns:
    def test_endpoint_and_key_change_requeues_by_default(self, client, openai_primary):
        r = client.put('/api/v1/settings/providers/openai',
                       json={'apiKey': 'sk-new-account-key', 'baseUrl': NEW_BASE})
        assert r.status_code == 200
        body = r.get_json()
        assert body['accountChanged'] is True
        assert body['affectedRuns']['action'] == 'requeue'
        assert body['affectedRuns']['count'] == 1
        assert body['affectedRuns']['runs'][0]['runId'] == 'run-1'
        # The frozen routes are gone, so a resumed run re-resolves them.
        assert _persisted_snapshot(openai_primary) is None

    def test_cancel_action_requests_cancellation(self, client, openai_primary):
        with patch('cancel.request_cancellation') as cancel:
            r = client.put('/api/v1/settings/providers/openai',
                           json={'baseUrl': NEW_BASE, 'affectedRunsAction': 'cancel'})
        assert r.status_code == 200
        assert r.get_json()['affectedRuns']['action'] == 'cancel'
        cancel.assert_called_once_with('account-change-feed', 'a1b2c3d4e5f6')
        assert _persisted_snapshot(openai_primary) is None

    def test_same_account_key_rotation_reports_no_change(self, client, openai_primary):
        r = client.put('/api/v1/settings/providers/openai',
                       json={'apiKey': 'sk-rotated-same-account'})
        assert r.status_code == 200
        body = r.get_json()
        assert 'accountChanged' not in body
        assert 'affectedRuns' not in body
        assert _persisted_snapshot(openai_primary) is not None

    def test_key_only_rotation_preserves_primary_and_secondary_models(
            self, client, primary_stage_models):
        db = primary_stage_models

        r = client.put('/api/v1/settings/providers/openai',
                       json={'apiKey': 'sk-rotated-same-account'})

        assert r.status_code == 200
        assert db.get_setting('claude_model') == 'detection-model'
        assert db.get_setting('verification_model') == 'verification-model'
        assert db.get_setting('chapters_model') == 'chapters-model'
        assert db.get_setting('review_model') == 'review-model'
        assert db.get_setting('pattern_cleanup_model') == 'cleanup-model'

    def test_endpoint_change_clears_models_bound_to_primary_only(
            self, client, primary_stage_models):
        db = primary_stage_models
        with patch('api.settings.trigger_reviewer_calibration'):
            r = client.put('/api/v1/settings/providers/openai', json={
                'apiKey': 'sk-new-account-key', 'baseUrl': NEW_BASE,
            })

        assert r.status_code == 200
        assert r.get_json()['accountChanged'] is True
        assert db.get_secret('openai_api_key') == 'sk-new-account-key'
        assert db.get_setting('claude_model') == ''
        assert db.get_setting('verification_model') == 'verification-model'
        assert db.get_setting('chapters_model') == ''
        assert db.get_setting('review_model') == ''
        assert db.get_setting('pattern_cleanup_model') == ''

    def test_endpoint_change_preserves_review_inheritance_sentinel(
            self, client, primary_stage_models):
        db = primary_stage_models
        db.set_setting('review_model', 'same_as_pass', is_default=False)
        with patch('api.settings.trigger_reviewer_calibration'):
            r = client.put('/api/v1/settings/providers/openai', json={'baseUrl': NEW_BASE})

        assert r.status_code == 200
        assert db.get_setting('claude_model') == ''
        assert db.get_setting('review_model') == 'same_as_pass'

    def test_endpoint_change_preserves_inherited_cleanup_model_row(
            self, client, primary_stage_models):
        db = primary_stage_models
        db.clear_setting('pattern_cleanup_model')
        with patch('api.settings.trigger_reviewer_calibration'):
            r = client.put('/api/v1/settings/providers/openai',
                           json={'baseUrl': NEW_BASE})

        assert r.status_code == 200
        assert db.get_setting('pattern_cleanup_model') is None

    def test_mixed_slot_route_does_not_reuse_secondary_model_after_primary_change(
            self, client, primary_stage_models):
        db = primary_stage_models
        db.set_setting('detection_provider', 'secondary', is_default=False)
        db.set_setting('claude_model', 'secondary-detection-model', is_default=False)
        db.set_setting('chapters_provider', 'primary', is_default=False)
        db.set_setting('chapters_model', 'primary-chapter-model', is_default=False)
        with patch('api.settings.trigger_reviewer_calibration'):
            r = client.put('/api/v1/settings/providers/openai',
                           json={'baseUrl': NEW_BASE})

        assert r.status_code == 200
        assert db.get_setting('claude_model') == 'secondary-detection-model'
        assert db.get_setting('chapters_model') == ''
        assert db.get_setting('pattern_cleanup_model') == ''
        with pytest.raises(ModelNotConfiguredError, match='chapters_model'):
            resolve_route('chapters')
        with pytest.raises(ModelNotConfiguredError, match='pattern_cleanup_model'):
            resolve_route('pattern_cleanup')

    def test_delete_endpoint_clears_models_bound_to_primary(self, client, primary_stage_models):
        db = primary_stage_models
        with patch('api.settings.trigger_reviewer_calibration'):
            r = client.delete('/api/v1/settings/providers/openai')

        assert r.status_code == 200
        assert r.get_json()['accountChanged'] is True
        assert db.get_setting('openai_base_url') == ''
        assert db.get_secret('openai_api_key') is None
        assert db.get_setting('claude_model') == ''
        assert db.get_setting('verification_model') == 'verification-model'
        assert db.get_setting('chapters_model') == ''
        assert db.get_setting('review_model') == ''
        assert db.get_setting('pattern_cleanup_model') == ''

    def test_unknown_action_is_rejected(self, client, openai_primary):
        r = client.put('/api/v1/settings/providers/openai',
                       json={'baseUrl': NEW_BASE, 'affectedRunsAction': 'drain'})
        assert r.status_code == 400
        assert _persisted_snapshot(openai_primary) is not None
