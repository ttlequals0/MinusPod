"""Per-run route snapshot resolution/persistence and per-phase provider
routing (checkpoint 02, task 3): a run resolves one immutable, non-secret
route per phase at start, persists it to processing_runs, reuses it on
recovery instead of re-resolving from live settings, and each phase's LLM
call goes through its own route's client and model.
"""
import json
import os
import sys
from unittest.mock import MagicMock

import pytest

pytest.importorskip("ctranslate2", reason="Integration tests require Docker environment")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

import run_context  # noqa: E402
import secrets_crypto  # noqa: E402
from ad_detector import AdDetector  # noqa: E402
from ad_reviewer import AdReviewer  # noqa: E402
import chapters_generator  # noqa: E402
from chapters_generator import ChaptersGenerator  # noqa: E402
from llm_capabilities import PASS_AD_DETECTION_1, PASS_AD_DETECTION_2  # noqa: E402
from llm_client import invalidate_provider_cache  # noqa: E402
from main_app import processing  # noqa: E402
from processing_queue import _pid_start_time  # noqa: E402


@pytest.fixture(autouse=True)
def _crypto(monkeypatch):
    monkeypatch.setenv('MINUSPOD_MASTER_PASSPHRASE', 'test-pass')
    secrets_crypto.reset_cache()
    yield
    secrets_crypto.reset_cache()


ROUTE_SETTING_KEYS = (
    'detection_provider', 'claude_model',
    'verification_provider', 'verification_model',
    'chapters_provider', 'chapters_model',
    'review_provider', 'review_model',
    'secondary_provider_enabled', 'secondary_provider',
)


@pytest.fixture
def mixed_provider_settings():
    """detection/verification/chapters=primary (anthropic), review=secondary
    (openrouter), on the same real Database singleton main_app.processing
    already holds. Stage settings are SLOT values (checkpoint 02b task 1),
    resolved against llm_provider=anthropic (primary) and
    secondary_provider=openrouter. These settings are read directly (not
    through the cached effective-provider fallback), so no cache
    invalidation race with other tests."""
    from api import get_database
    db = get_database()
    saved = {key: db.get_setting(key) for key in ROUTE_SETTING_KEYS}
    db.set_setting('detection_provider', 'primary', is_default=False)
    db.set_setting('claude_model', 'claude-detect', is_default=False)
    # Pinned explicitly (not left to fall back to claude_model) so this test
    # is immune to other test modules leaving a stale value on the shared
    # real Database singleton this fixture does not otherwise touch.
    db.set_setting('verification_model', 'claude-detect', is_default=False)
    db.set_setting('chapters_model', 'claude-detect', is_default=False)
    db.set_setting('verification_provider', 'primary', is_default=False)
    db.set_setting('chapters_provider', 'primary', is_default=False)
    db.set_setting('secondary_provider_enabled', 'true', is_default=False)
    db.set_setting('secondary_provider', 'openrouter', is_default=False)
    db.set_setting('review_provider', 'secondary', is_default=False)
    db.set_setting('review_model', 'or-review-model', is_default=False)
    invalidate_provider_cache()
    yield db
    for key, value in saved.items():
        if value is None:
            db.clear_setting(key)
        else:
            db.set_setting(key, value, is_default=False)
    invalidate_provider_cache()


@pytest.fixture
def run_row(app_client, mixed_provider_settings):
    """A podcast/episode/processing_runs row owned by this test process, so
    _check_cancel's ownership poll (owner_pid + owner_pid_start) matches."""
    db = mixed_provider_settings
    slug = 'mixed-provider-feed'
    episode_id = 'aa11bb22cc33'
    db.create_podcast(slug, 'https://example.com/feed.xml', 'Mixed Provider Feed')
    db.upsert_episode(slug, episode_id, original_url='https://example.com/ep.mp3',
                      title='Test Episode', status='pending')
    podcast = db.get_podcast_by_slug(slug)
    run_id = 'run-mixed-provider-1'
    owner_pid = os.getpid()
    conn = db.get_connection()
    conn.execute(
        "INSERT INTO processing_runs "
        "(run_id, podcast_id, episode_id, owner_pid, owner_pid_start, state) "
        "VALUES (?, ?, ?, ?, ?, 'running')",
        (run_id, podcast['id'], episode_id, owner_pid, _pid_start_time(owner_pid)),
    )
    conn.commit()
    yield {'run_id': run_id, 'slug': slug, 'episode_id': episode_id, 'db': db}
    try:
        db.delete_podcast(slug)
    except Exception:
        pass


def _persisted_snapshot_raw(db, run_id):
    row = db.get_connection().execute(
        "SELECT route_snapshot_json FROM processing_runs WHERE run_id = ?",
        (run_id,),
    ).fetchone()
    return row['route_snapshot_json'] if row else None


class TestRouteSnapshotResolutionAndPersistence:
    def test_resolved_and_persisted_with_no_secrets(self, run_row):
        db = run_row['db']
        db.set_secret('anthropic_api_key', 'sk-ant-should-not-appear')
        db.set_secret('openrouter_api_key', 'sk-or-should-not-appear')

        snapshot = processing._resolve_or_load_route_snapshot(run_row['run_id'])

        assert snapshot['detection'] == {
            'provider_key': 'anthropic', 'configured_model': 'claude-detect',
            'base_url': None, 'credential_slot': 'primary'}
        assert snapshot['verification'] == {
            'provider_key': 'anthropic', 'configured_model': 'claude-detect',
            'base_url': None, 'credential_slot': 'primary'}
        assert snapshot['chapters'] == {
            'provider_key': 'anthropic', 'configured_model': 'claude-detect',
            'base_url': None, 'credential_slot': 'primary'}
        assert snapshot['review'] == {
            'provider_key': 'openrouter', 'configured_model': 'or-review-model',
            'base_url': 'https://openrouter.ai/api/v1', 'credential_slot': 'secondary',
            'gate': {'review_provider': 'secondary', 'review_model': 'or-review-model'}}

        raw = _persisted_snapshot_raw(db, run_row['run_id'])
        assert raw is not None
        assert json.loads(raw) == snapshot
        assert 'sk-ant-should-not-appear' not in raw
        assert 'sk-or-should-not-appear' not in raw

    def test_review_route_follows_openrouter_when_review_provider_explicit(self, run_row):
        # Same fixture, spelled out for the checkpoint's "repeat with
        # review=openrouter" case: review already targets openrouter here
        # (mixed_provider_settings), distinct from detection's anthropic.
        snapshot = processing._resolve_or_load_route_snapshot(run_row['run_id'])
        assert snapshot['detection']['provider_key'] == 'anthropic'
        assert snapshot['review']['provider_key'] == 'openrouter'
        assert snapshot['detection']['provider_key'] != snapshot['review']['provider_key']

    def test_recovery_reuses_persisted_snapshot_over_current_settings(self, run_row):
        db = run_row['db']
        first = processing._resolve_or_load_route_snapshot(run_row['run_id'])
        assert first['detection'] == {
            'provider_key': 'anthropic', 'configured_model': 'claude-detect',
            'base_url': None, 'credential_slot': 'primary'}

        # A settings change mid-run must not retroactively alter an
        # already-persisted snapshot: recovery re-reads the row instead of
        # re-resolving from the (now different) live settings.
        db.set_setting('detection_provider', 'secondary', is_default=False)
        db.set_setting('claude_model', 'or-detect-model', is_default=False)
        invalidate_provider_cache()

        second = processing._resolve_or_load_route_snapshot(run_row['run_id'])
        assert second == first


class TestProcessEpisodeWiresSnapshotAtRunStart:
    def test_ctx_route_snapshot_set_before_any_phase_runs(self, run_row, monkeypatch):
        """process_episode resolves+sets the snapshot on ctx immediately
        after start_episode_token_tracking(), before touching episode data.
        A fake db.get_episode raises right after that point so the test
        never reaches transcription/detection."""
        fake_db = MagicMock()
        fake_db.get_episode.side_effect = RuntimeError('stop-after-routing')
        monkeypatch.setattr(processing, 'db', fake_db)

        slug, episode_id, run_id = (
            run_row['slug'], run_row['episode_id'], run_row['run_id'])
        ctx = run_context.begin(slug, episode_id, run_id=run_id)
        try:
            with pytest.raises(RuntimeError, match='stop-after-routing'):
                processing.process_episode(
                    slug, episode_id, 'https://example.com/ep.mp3', run_id=run_id)
            assert ctx.route_snapshot['detection'] == {
                'provider_key': 'anthropic', 'configured_model': 'claude-detect',
                'base_url': None, 'credential_slot': 'primary'}
            assert ctx.route_snapshot['review'] == {
                'provider_key': 'openrouter', 'configured_model': 'or-review-model',
                'base_url': 'https://openrouter.ai/api/v1', 'credential_slot': 'secondary',
                'gate': {'review_provider': 'secondary', 'review_model': 'or-review-model'}}
        finally:
            run_context.end(ctx)


class TestPhasesUseTheirRoutedClientAndModel:
    def test_detection_and_verification_use_their_routed_client_and_model(self, monkeypatch):
        calls = []

        def fake_get_client_for_provider(provider_key, base_url=None,
                                         credential_slot='primary', force_new=False):
            calls.append(provider_key)
            return MagicMock()

        monkeypatch.setattr('ad_detector.get_client_for_provider',
                            fake_get_client_for_provider)

        ctx = run_context.begin('mixed-provider-feed', 'ep-detect', run_id='r-detect')
        try:
            ctx.set_route_snapshot({
                'detection': {'provider_key': 'anthropic', 'configured_model': 'claude-detect',
                              'base_url': None, 'credential_slot': 'primary'},
                'verification': {'provider_key': 'openrouter', 'configured_model': 'or-verify',
                                 'base_url': 'https://openrouter.ai/api/v1', 'credential_slot': 'secondary'},
            })
            detector = AdDetector(api_key='test-key')

            assert detector.get_model() == 'claude-detect'
            assert detector.get_verification_model() == 'or-verify'
            assert detector.get_provider() == 'anthropic'
            assert detector.get_verification_provider() == 'openrouter'

            detector._client_for_pass(PASS_AD_DETECTION_1)
            detector._client_for_pass(PASS_AD_DETECTION_2)
            assert calls == ['anthropic', 'openrouter']
        finally:
            run_context.end(ctx)

    def test_reviewer_uses_its_routed_client_and_model(self, run_row, monkeypatch):
        db = run_row['db']
        calls = []
        fake_client = MagicMock()
        fake_client.messages_create.return_value = MagicMock(
            content='[{"start": 10.0, "end": 20.0, "confidence": 0.9, "reason": "ad"}]')

        def fake_get_client_for_provider(provider_key, base_url=None,
                                         credential_slot='primary', force_new=False):
            calls.append(provider_key)
            return fake_client

        monkeypatch.setattr('ad_reviewer.get_client_for_provider',
                            fake_get_client_for_provider)

        reviewer = AdReviewer(db=db, sponsor_service=None)
        ad = {'start': 10.0, 'end': 20.0, 'confidence': 0.9}
        result = reviewer.review(
            accepted_ads=[ad], resurrection_eligible=[],
            segments=[{'start': 0.0, 'end': 30.0, 'text': 'ad content here'}],
            episode_meta={
                'slug': 's', 'episode_id': 'e', 'podcast_name': 'p',
                'episode_title': 't', 'episode_description': '',
                'podcast_description': '',
            },
            pass_num=1, pass_model='claude-detect', pass_provider='anthropic',
        )

        # review_provider=secondary (mixed_provider_settings): the reviewer's
        # route diverges from the pass-1 provider it was handed.
        assert calls == ['openrouter']
        assert result.verdicts
        assert result.verdicts[0].model_used == 'or-review-model'

    def test_chapters_generator_uses_its_routed_client_and_model(self, monkeypatch):
        calls = []

        def fake_get_client_for_provider(provider_key, base_url=None,
                                         credential_slot='primary', force_new=False):
            calls.append(provider_key)
            return MagicMock()

        monkeypatch.setattr('chapters_generator.get_client_for_provider',
                            fake_get_client_for_provider)

        ctx = run_context.begin('mixed-provider-feed', 'ep-chapters', run_id='r-chapters')
        try:
            ctx.set_route_snapshot({
                'chapters': {'provider_key': 'ollama', 'configured_model': 'local-chapters',
                             'base_url': 'http://localhost:11434/v1', 'credential_slot': 'primary'},
            })
            assert chapters_generator.get_chapters_model() == 'local-chapters'

            gen = ChaptersGenerator(api_key='test-key')
            client = gen._llm_client

            assert calls == ['ollama']
            assert client is not None
        finally:
            run_context.end(ctx)


def _reviewer_call_kwargs(pass_num, pass_model, pass_provider):
    return {
        'accepted_ads': [{'start': 10.0, 'end': 20.0, 'confidence': 0.9}],
        'resurrection_eligible': [],
        'segments': [{'start': 0.0, 'end': 30.0, 'text': 'ad content here'}],
        'episode_meta': {
            'slug': 's', 'episode_id': 'e', 'podcast_name': 'p',
            'episode_title': 't', 'episode_description': '',
            'podcast_description': '',
        },
        'pass_num': pass_num, 'pass_model': pass_model, 'pass_provider': pass_provider,
    }


def _patch_reviewer_client(monkeypatch, calls):
    fake_client = MagicMock()
    fake_client.messages_create.return_value = MagicMock(
        content='[{"start": 10.0, "end": 20.0, "confidence": 0.9, "reason": "ad"}]')

    def fake_get_client_for_provider(provider_key, base_url=None,
                                     credential_slot='primary', force_new=False):
        calls.append(provider_key)
        return fake_client

    monkeypatch.setattr('ad_reviewer.get_client_for_provider',
                        fake_get_client_for_provider)


class TestReviewerGateFrozenAtRunStart:
    """The reviewer's route must not re-read review_provider/review_model
    live: a mid-run operator change must not affect a run already in
    flight. The gate frozen into the snapshot's 'review' entry at run start
    is what AdReviewer._resolve_route consults instead."""

    def test_explicit_review_provider_survives_live_setting_change_before_pass2(
            self, run_row, monkeypatch):
        db = run_row['db']
        run_id = run_row['run_id']

        # Snapshot resolved once at run start: review_provider=secondary,
        # review_model=or-review-model (mixed_provider_settings).
        snapshot = processing._resolve_or_load_route_snapshot(run_id)
        assert snapshot['review']['gate'] == {
            'review_provider': 'secondary', 'review_model': 'or-review-model'}

        ctx = run_context.begin(run_row['slug'], run_row['episode_id'], run_id=run_id)
        ctx.set_route_snapshot(snapshot)
        try:
            # Operator changes review_provider well after pass-1 review
            # would already have run on openrouter.
            db.set_setting('review_provider', 'primary', is_default=False)
            db.set_setting('review_model', 'local-review-model', is_default=False)

            calls = []
            _patch_reviewer_client(monkeypatch, calls)
            reviewer = AdReviewer(db=db, sponsor_service=None)

            # Pass-2 context deliberately differs from pass-1's, to prove
            # the frozen gate (not the live setting, not the pass) pins the
            # provider when review_provider is explicit.
            result = reviewer.review(**_reviewer_call_kwargs(
                pass_num=2, pass_model='or-verify-model', pass_provider='anthropic'))

            assert calls == ['openrouter']
            assert result.verdicts
            assert result.verdicts[0].model_used == 'or-review-model'
        finally:
            run_context.end(ctx)

    def test_same_as_pass_gate_still_inherits_the_calling_pass_route(
            self, run_row, monkeypatch):
        db = run_row['db']
        run_id = run_row['run_id']
        db.clear_setting('review_provider')
        db.clear_setting('review_model')

        snapshot = processing._resolve_or_load_route_snapshot(run_id)
        assert snapshot['review']['gate'] == {
            'review_provider': None, 'review_model': None}

        ctx = run_context.begin(run_row['slug'], run_row['episode_id'], run_id=run_id)
        ctx.set_route_snapshot(snapshot)
        try:
            calls = []
            _patch_reviewer_client(monkeypatch, calls)
            reviewer = AdReviewer(db=db, sponsor_service=None)

            reviewer.review(**_reviewer_call_kwargs(
                pass_num=1, pass_model='claude-detect', pass_provider='anthropic'))
            reviewer.review(**_reviewer_call_kwargs(
                pass_num=2, pass_model='or-verify-model', pass_provider='openrouter'))

            # same_as_pass in the frozen gate still tracks whichever pass is
            # actually calling, not a single value pinned at run start.
            assert calls == ['anthropic', 'openrouter']
        finally:
            run_context.end(ctx)
