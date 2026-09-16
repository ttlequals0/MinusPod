"""Run-scoped fences in the pipeline: a 429 with no provider key is scoped
from the run's route snapshot, the transcriber's shared stats field is read
atomically, the ownership check runs before any output path is resolved, and
an aborted run deletes the render it never published.
"""
from contextlib import ExitStack
from unittest.mock import patch

import pytest

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('processing_fences_test_')
import run_context
from cancel import ProcessingOwnershipLost
from llm_client import ProviderRateLimitedError
from main_app import db
import main_app.processing as processing
from main_app.processing import _handle_processing_failure
from rate_limit_hold import resolve_hold_scope

SLUG = 'processing-fences-feed'
EP = 'aa11bb22cc33'

SECONDARY_SNAPSHOT = {
    phase: {'provider_key': 'provider-b', 'configured_model': 'model-x',
            'credential_slot': 'secondary'}
    for phase in ('detection', 'review', 'verification', 'chapters')
}


@pytest.fixture
def run_ctx():
    ctx = run_context.begin(SLUG, EP)
    ctx.set_route_snapshot(SECONDARY_SNAPSHOT)
    yield ctx
    run_context.end(ctx)


def _limit_error(provider_key=None, phase=None):
    return ProviderRateLimitedError('429', retry_after_seconds=1.0,
                                    provider_key=provider_key, phase=phase)


class TestHoldScope:
    def test_error_provider_wins(self, run_ctx):
        assert resolve_hold_scope(
            _limit_error('provider-a'), 'chapters') == ('provider-a', 'primary')

    def test_missing_provider_comes_from_the_phase_route(self, run_ctx):
        assert resolve_hold_scope(
            _limit_error(), 'chapters') == ('provider-b', 'secondary')

    def test_phase_carried_on_the_error_needs_no_argument(self, run_ctx):
        assert resolve_hold_scope(
            _limit_error(phase='chapters')) == ('provider-b', 'secondary')

    def test_missing_provider_and_phase_uses_the_run_wide_route(self, run_ctx):
        assert resolve_hold_scope(_limit_error()) == ('provider-b', 'secondary')

    def test_mixed_routes_with_no_phase_stay_unscoped(self):
        ctx = run_context.begin(SLUG, EP)
        try:
            ctx.set_route_snapshot({
                'detection': {'provider_key': 'provider-a', 'credential_slot': 'primary'},
                'chapters': {'provider_key': 'provider-b', 'credential_slot': 'secondary'},
            })
            assert resolve_hold_scope(_limit_error()) == (None, 'primary')
        finally:
            run_context.end(ctx)

    def test_no_snapshot_stays_unscoped(self):
        assert resolve_hold_scope(_limit_error(), 'chapters') == (None, 'primary')


@pytest.fixture
def seeded_episode():
    db.create_podcast(SLUG, 'https://example.com/feed.xml', title='Fences')
    db.upsert_episode(SLUG, EP, title='Episode', status='processing',
                      original_url='https://example.com/e.mp3')
    db.set_setting('rate_limit_hold_enabled', 'true')
    yield
    db.set_setting('rate_limit_hold_enabled', 'false')
    from rate_limit_hold import _provider_hold_suffixes, clear_hold
    for provider in _provider_hold_suffixes(db):
        clear_hold(db, provider)
    db.set_setting('rate_limit_hold_until', '')
    db.clear_setting('rate_limit_hold_since')
    db.delete_podcast(SLUG)
    db.get_connection().execute("DELETE FROM auto_process_queue")
    db.get_connection().commit()


class TestFailureHandlerScopesTheHold:
    def test_429_without_provider_key_records_a_secondary_scoped_hold(
            self, seeded_episode, run_ctx):
        from rate_limit_hold import get_active_hold
        error = ProviderRateLimitedError('resets soon', retry_after_seconds=600.0)
        with patch('main_app.processing.status_service'):
            _handle_processing_failure(
                SLUG, EP, 'Episode', 'Fences', db.get_episode(SLUG, EP),
                error, start_time=0.0)
        assert get_active_hold(db, 'provider-b', 'secondary')[0]
        assert get_active_hold(db)[0] is None


_SEGMENTS = [{'start': 0.0, 'end': 5.0, 'text': 'hello'}]


class _Transcriber:
    """Stand-in for the shared transcriber singleton."""
    def __init__(self):
        self.last_transcription_stats = None


def _run_pipeline(stack, *, transcribe=None, owner_check=None, required=[]):
    """Drive process_episode through a stubbed standard run and return the
    mocks the assertions need. Mirrors test_provider_budget_reconcile's harness.
    """
    podcast_row = {'id': 1, 'slug': SLUG, 'description': None, 'tags': None,
                   'dai_platform': None, 'passthrough_enabled': None,
                   'skip_ad_detection': None, 'skip_second_pass': None}
    p = lambda *a, **k: stack.enter_context(patch.object(*a, **k))
    mock_db = p(processing, 'db')
    p(processing, 'status_service')
    storage = p(processing, 'storage')
    audio_processor = p(processing, 'audio_processor')
    p(processing, 'transcriber', _Transcriber())
    p(processing.ad_detector, 'get_model', return_value='test-model')
    p(processing.ad_detector, 'get_verification_model', return_value='test-model')
    p(processing, 'start_episode_token_tracking')
    p(processing, 'get_available_memory_gb', return_value=None)
    p(processing, 'get_min_cut_confidence', return_value=0.8)
    p(processing, '_download_and_transcribe',
      side_effect=transcribe or (lambda *a, **kw: ('/tmp/fences.mp3', _SEGMENTS)))
    p(processing, '_run_differential_fetch', return_value=None)
    p(processing, '_run_audio_analysis', return_value=None)
    p(processing, 'load_positional_prior', return_value=None)
    p(processing, '_detect_ads_first_pass', return_value=([], 0, None))
    p(processing, '_refine_and_validate', return_value=([], []))
    p(processing, '_run_ad_reviewer', return_value=([], []))
    p(processing, '_snap_terminal_starts', return_value=[])
    p(processing, '_complete_cut_tails', return_value=[])
    local_ap_cls = p(processing, 'AudioProcessor')
    p(processing, '_run_verification_pass',
      return_value=(0, [], [], [], '/tmp/cut.mp3', 0, True, 0))
    p(processing, '_generate_assets')
    finalize = p(processing, '_finalize_episode')
    p(processing, '_required_providers_for_admission', return_value=required)
    p(processing, 'get_effective_provider', return_value='provider-a')
    p(processing.shutil, 'move')
    unlink = p(processing.os, 'unlink')
    p(processing.os.path, 'exists', return_value=False)
    if owner_check is not None:
        p(processing, '_require_publication_owner', side_effect=owner_check)

    mock_db.get_episode.return_value = {}
    mock_db.get_podcast_by_slug.return_value = podcast_row
    mock_db.get_setting.return_value = 'false'
    mock_db.get_all_settings.return_value = {}
    mock_db.reserve_provider_spend.return_value = {
        'allowed': True, 'reservation_id': 'resv-1'}
    mock_db.run_provider_spend_is_incomplete.return_value = False
    audio_processor.get_audio_duration.return_value = 100.0
    local_ap = local_ap_cls.return_value
    local_ap.process_episode.return_value = ('/tmp/cut.mp3', [])
    local_ap.get_audio_duration.return_value = 100.0
    storage.get_episode_path.return_value = '/tmp/final.mp3'
    return {'storage': storage, 'finalize': finalize,
            'audio_processor': local_ap, 'db': mock_db, 'unlink': unlink}


class TestTranscriptionStatsReadAtomically:
    def test_recorded_stats_are_a_copy_of_the_shared_field(self):
        shared = {'device': 'cuda', 'batch_size': 8}

        def _transcribe(*a, **kw):
            processing.transcriber.last_transcription_stats = shared
            return ('/tmp/fences.mp3', _SEGMENTS)

        with ExitStack() as stack:
            mocks = _run_pipeline(stack, transcribe=_transcribe)
            processing.process_episode(SLUG, EP, 'https://example.com/e.mp3')
            run_stats = mocks['finalize'].call_args.kwargs['run_stats']
        # A later run mutating the shared field must not rewrite this run's stats.
        shared['batch_size'] = 1
        assert run_stats['transcription'] == {'device': 'cuda', 'batch_size': 8}


class TestReservationSkipsLlmFreeRuns:
    def test_no_required_account_reserves_nothing(self):
        """An empty requirement means the run makes no LLM call, so it must not
        charge the default provider's budget."""
        with ExitStack() as stack:
            mocks = _run_pipeline(stack)
            processing.process_episode(SLUG, EP, 'https://example.com/e.mp3')
        mocks['db'].reserve_provider_spend.assert_not_called()

    def test_unresolvable_requirement_still_reserves_the_default(self):
        with ExitStack() as stack:
            mocks = _run_pipeline(stack, required=None)
            processing.process_episode(SLUG, EP, 'https://example.com/e.mp3')
        mocks['db'].reserve_provider_spend.assert_called_once_with(
            'provider-a', None, run_id=None)


class TestOwnershipFenceBeforePathResolution:
    def test_lost_ownership_does_not_resolve_the_output_path(self):
        """A deleted feed must not have its podcast directory recreated by a
        still-running worker: get_episode_path mkdirs the tree."""
        state = {'cut': False}

        def _owner_check(slug, episode_id):
            if state['cut']:
                raise ProcessingOwnershipLost('feed deleted mid-run')

        def _cut(*args, **kwargs):
            state['cut'] = True
            return ('/tmp/cut.mp3', [])

        with ExitStack() as stack:
            mocks = _run_pipeline(stack, owner_check=_owner_check)
            mocks['audio_processor'].process_episode.side_effect = _cut
            with pytest.raises(ProcessingOwnershipLost):
                processing.process_episode(SLUG, EP, 'https://example.com/e.mp3')
            mocks['storage'].get_episode_path.assert_not_called()


class TestUnpublishedRenderIsCleanedUp:
    def test_a_verification_hold_deletes_the_cut_render(self):
        """A rate-limit hold aborts after the cut but before the move to
        final_path, so the run owns the temp render on its way out."""
        with ExitStack() as stack:
            mocks = _run_pipeline(stack)
            stack.enter_context(patch.object(
                processing, '_run_verification_pass',
                side_effect=ProviderRateLimitedError(
                    'provider rate limit reached', retry_after_seconds=4499.0,
                    phase='verification')))
            stack.enter_context(patch.object(
                processing.os.path, 'exists',
                side_effect=lambda path: path in ('/tmp/cut.mp3', '/tmp/fences.mp3')))
            assert processing.process_episode(
                SLUG, EP, 'https://example.com/e.mp3') is False
        mocks['unlink'].assert_any_call('/tmp/cut.mp3')

    def test_a_published_render_is_not_deleted(self):
        with ExitStack() as stack:
            mocks = _run_pipeline(stack)
            processing.process_episode(SLUG, EP, 'https://example.com/e.mp3')
        mocks['unlink'].assert_not_called()
