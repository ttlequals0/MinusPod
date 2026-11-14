"""Per-feed processing mode resolution and plumbing (#537).

passthrough_enabled, skip_ad_detection and detection_mode='keep_content' are independent DB columns.
resolve_feed_processing_mode collapses them to one mode; the truth table below pins the original
branch-order precedence (passthrough before skip, skip never consults detection_mode).
"""
import os
import sys
import tempfile
from contextlib import ExitStack

os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='procmode_test_'))
os.environ.setdefault('SECRET_KEY', 'test-secret')

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from unittest.mock import MagicMock, patch

import pytest

from config import (
    DETECTION_MODE_KEEP_CONTENT,
    PROCESSING_MODE_KEEP_CONTENT,
    PROCESSING_MODE_PASSTHROUGH,
    PROCESSING_MODE_SKIP_DETECTION,
    PROCESSING_MODE_STANDARD,
    PROCESSING_MODE_CUE_ONLY,
    DETECTION_MODE_CUE_ONLY,
    CUE_ONLY_SAFETY_HOLD_NEW,
    CUE_ONLY_SAFETY_AUTO_CUT,
    resolve_feed_processing_mode,
    resolve_processing_mode,
    resolve_skip_transcription,
    resolve_cue_only_safety,
)
from ad_detector import AdDetector
import main_app.processing as processing
from main_app.pipeline import common, transcribe, signals, detect, admission, recut
import run_context
from tests.unit.pipeline_run_support import _INACTIVE_FAILOVER_STATE, _PROBE_PHASES_UNSET, _row, _run_pipeline  # noqa: F401
from api.feeds import _normalize_processing_mode, _normalize_detection_mode

SEGMENTS = [{'start': 0.0, 'end': 5.0, 'text': 'hello'},
            {'start': 5.0, 'end': 10.0, 'text': 'world'}]


class TestResolveFeedProcessingMode:
    # All 8 column combinations; expected values match the pipeline branch
    # ordering before centralization (passthrough > skip > keep_content).
    @pytest.mark.parametrize('pt,skip,mode,expected', [
        (None, None, None, PROCESSING_MODE_STANDARD),
        (None, None, DETECTION_MODE_KEEP_CONTENT, PROCESSING_MODE_KEEP_CONTENT),
        (None, 1, None, PROCESSING_MODE_SKIP_DETECTION),
        (None, 1, DETECTION_MODE_KEEP_CONTENT, PROCESSING_MODE_SKIP_DETECTION),
        (1, None, None, PROCESSING_MODE_PASSTHROUGH),
        (1, None, DETECTION_MODE_KEEP_CONTENT, PROCESSING_MODE_PASSTHROUGH),
        (1, 1, None, PROCESSING_MODE_PASSTHROUGH),
        (1, 1, DETECTION_MODE_KEEP_CONTENT, PROCESSING_MODE_PASSTHROUGH),
    ])
    def test_eight_column_combinations(self, pt, skip, mode, expected):
        row = {'passthrough_enabled': pt, 'skip_ad_detection': skip,
               'detection_mode': mode}
        assert resolve_feed_processing_mode(row) == expected

    def test_missing_row_is_standard(self):
        assert resolve_feed_processing_mode(None) == PROCESSING_MODE_STANDARD

    def test_missing_keys_are_standard(self):
        # get_podcast_by_slug always selects p.*, but a stub row in tests may
        # omit the columns; .get() semantics keep that safe.
        assert resolve_feed_processing_mode({'id': 1}) == PROCESSING_MODE_STANDARD

    def test_zero_flags_match_null_flags(self):
        row = {'passthrough_enabled': 0, 'skip_ad_detection': 0,
               'detection_mode': None}
        assert resolve_feed_processing_mode(row) == PROCESSING_MODE_STANDARD

    @pytest.mark.parametrize('mode', ['blacklist', 'bogus', ''])
    def test_non_keep_content_modes_are_standard(self, mode):
        # Mirrors resolve_detection_mode: only the exact 'keep_content'
        # value opts in; a bad stored value can never enable content cutting.
        row = {'passthrough_enabled': None, 'skip_ad_detection': None,
               'detection_mode': mode}
        assert resolve_feed_processing_mode(row) == PROCESSING_MODE_STANDARD


class TestResolveProcessingMode:
    """Per-episode pass-through override (issue #746): wins over the feed
    mode when set, otherwise the feed mode resolver applies unchanged."""

    def test_episode_flag_wins_over_standard_feed(self):
        row = _row()  # standard feed (all flags unset)
        assert resolve_processing_mode(row, {'passthrough_enabled': 1}) == \
            PROCESSING_MODE_PASSTHROUGH

    def test_episode_flag_wins_over_skip_detection_feed(self):
        row = _row(skip=1)
        assert resolve_processing_mode(row, {'passthrough_enabled': 1}) == \
            PROCESSING_MODE_PASSTHROUGH

    def test_episode_flag_unset_falls_back_to_feed_mode(self):
        row = _row(skip=1)
        assert resolve_processing_mode(row, {'passthrough_enabled': 0}) == \
            PROCESSING_MODE_SKIP_DETECTION
        assert resolve_processing_mode(row, {}) == PROCESSING_MODE_SKIP_DETECTION

    def test_no_episode_row_falls_back_to_feed_mode(self):
        row = _row(skip=1)
        assert resolve_processing_mode(row, None) == PROCESSING_MODE_SKIP_DETECTION

    def test_feed_already_passthrough_is_unaffected(self):
        row = _row(pt=1)
        assert resolve_processing_mode(row, {'passthrough_enabled': 0}) == \
            PROCESSING_MODE_PASSTHROUGH


class TestProcessEpisodeModePlumbing:
    def test_passthrough_wins_over_skip_and_keep_content(self):
        with patch.object(processing, 'db') as db, \
             patch.object(recut, '_passthrough_episode') as pt, \
             patch.object(processing, 'start_episode_token_tracking'), \
             patch.object(processing.failover, 'run_probe_targets') as select_probes, \
             patch.object(processing.failover, 'ensure_fresh_probes') as ensure_probes:
            db.get_episode.return_value = {}
            db.get_podcast_by_slug.return_value = _row(
                pt=1, skip=1, mode=DETECTION_MODE_KEEP_CONTENT)
            db.get_podcast_row.return_value = db.get_podcast_by_slug.return_value
            pt.return_value = True
            result = processing.process_episode(
                'mode-feed', 'ep1', 'https://example.com/ep1.mp3')
        assert result is True
        pt.assert_called_once()
        select_probes.assert_not_called()
        ensure_probes.assert_not_called()

    def test_episode_passthrough_flag_routes_to_passthrough_on_standard_feed(self):
        # Issue #746: the per-episode flag must win even though the feed
        # itself is in standard mode (no feed-level flags set).
        with patch.object(processing, 'db') as db, \
             patch.object(recut, '_passthrough_episode') as pt, \
             patch.object(processing, 'start_episode_token_tracking'):
            db.get_episode.return_value = {'passthrough_enabled': 1}
            db.get_podcast_by_slug.return_value = _row()
            db.get_podcast_row.return_value = db.get_podcast_by_slug.return_value
            pt.return_value = True
            result = processing.process_episode(
                'mode-feed', 'ep1', 'https://example.com/ep1.mp3')
        assert result is True
        pt.assert_called_once()

    def test_skip_wins_over_keep_content(self):
        m = _run_pipeline(_row(skip=1, mode=DETECTION_MODE_KEEP_CONTENT))
        assert m['result'] is True
        m['detect'].assert_not_called()
        assert m['verify'].call_args.kwargs['skip_verification'] is True

        # The pipeline passes keep_content=None so the detector resolves the mode from the DB at detection
        # time; a detection_mode toggle made during the long download/transcription window must be honored.
    def test_keep_content_mode_defers_resolution_to_detector(self):
        m = _run_pipeline(_row(mode=DETECTION_MODE_KEEP_CONTENT))
        assert m['result'] is True
        assert m['detect'].call_args.kwargs['keep_content'] is None
        assert m['verify'].call_args.kwargs['skip_verification'] is False

    def test_standard_mode_defers_resolution_to_detector(self):
        m = _run_pipeline(_row())
        assert m['result'] is True
        assert m['detect'].call_args.kwargs['keep_content'] is None
        assert m['verify'].call_args.kwargs['skip_verification'] is False

    def test_transcript_spans_reach_detection_and_validation(self):
        span = {'start': 10.0, 'end': 40.0, 'words': 80, 'offset_confirmed': False}
        with patch.object(signals, '_run_transcript_diff',
                          return_value={'status': 'ok', 'spans': [span]}) as stage:
            m = _run_pipeline(_row())
        stage.assert_called_once()
        assert m['detect'].call_args.kwargs['transcript_spans'] == [span]
        assert m['refine'].call_args.kwargs['transcript_spans'] == [span]

    def test_unreliable_transcript_diff_passes_no_spans(self):
        with patch.object(signals, '_run_transcript_diff',
                          return_value={'status': 'unreliable', 'spans': []}):
            m = _run_pipeline(_row())
        assert m['detect'].call_args.kwargs['transcript_spans'] == []

    def test_transcript_fetch_error_does_not_stop_the_run(self):
        row = dict(_row(), transcript_differential=1)
        episode = {'upstream_transcript_url': 'https://cdn.example.com/ep1.vtt',
                   'upstream_transcript_type': 'text/vtt'}
        with patch.object(signals, 'fetch_upstream_transcript',
                          side_effect=RuntimeError('upstream down')) as fetch:
            m = _run_pipeline(row, episode_row=episode)
        fetch.assert_called_once()
        assert m['result'] is True
        assert m['detect'].call_args.kwargs['transcript_spans'] == []
        m['finalize'].assert_called_once()
        saved = m['db'].save_episode_upstream_transcript.call_args.args[2]
        assert saved['status'] == 'error'

    def test_skip_detection_skips_transcript_diff(self):
        with patch.object(signals, '_run_transcript_diff') as stage:
            _run_pipeline(_row(skip=1))
        stage.assert_not_called()

    def test_pre_run_probe_selection_uses_effective_enabled_routes(self):
        phases = {
            'detection': {'credential_slot': 'secondary'},
            'review': {'credential_slot': 'primary'},
        }
        snapshot = {
            'detection': {'credential_slot': 'secondary'},
            'review': {'credential_slot': 'primary'},
        }
        m = _run_pipeline(_row(), route_snapshot=snapshot, probe_phases=phases)
        m['probe_selector'].assert_called_once_with(phases, whisper_required=True)
        m['ensure_probes'].assert_called_once_with(['llm:secondary', 'llm:primary'])

    def test_cue_only_skip_transcription_omits_pre_run_probes(self):
        row = dict(_row(mode=DETECTION_MODE_CUE_ONLY), skip_transcription=1)
        m = _run_pipeline(row, route_snapshot={}, probe_phases={})
        m['probe_selector'].assert_called_once_with({}, whisper_required=False)
        m['ensure_probes'].assert_called_once_with([])

    def test_unresolved_active_phases_use_conservative_route_fallback(self):
        m = _run_pipeline(_row(), route_snapshot={}, probe_phases=None)
        m['probe_selector'].assert_called_once_with(None, whisper_required=True)
        m['ensure_probes'].assert_called_once_with([])

    def test_recut_skips_pre_run_probes(self):
        with patch.object(processing, 'db') as db, \
             patch.object(recut, '_recut_episode', return_value=True) as recut_mock, \
             patch.object(admission, '_resolve_or_load_route_snapshot', return_value={}), \
             patch.object(admission, '_assert_route_snapshot_current'), \
             patch.object(processing, 'start_episode_token_tracking'), \
             patch.object(processing.failover, 'run_probe_targets') as select_probes, \
             patch.object(processing.failover, 'ensure_fresh_probes') as ensure_probes:
            db.get_episode.return_value = {'reprocess_mode': 'recut'}
            result = processing.process_episode(
                'mode-feed', 'ep1', 'https://example.com/ep1.mp3')
        assert result is True
        recut_mock.assert_called_once()
        select_probes.assert_not_called()
        ensure_probes.assert_not_called()

    def test_records_disabled_normalization_in_run_stats(self):
        m = _run_pipeline(_row())
        assert m['result'] is True
        assert m['finalize'].call_args.kwargs['run_stats']['normalization_skipped'] is True

    def test_failover_recorded_before_approval_recut_success_exit(self):
        # #806: run_stats['failover'] must be set before _recut_episode is
        # called, since its success returns before the main finalize call.
        snapshot = {'detection': {'failover_from': 'primary',
                                  'provider_key': 'openai-compatible',
                                  'credential_slot': 'primary'}}
        m = _run_pipeline(_row(), approval_recut=True,
                           route_snapshot=snapshot, failover_state={}, standby_dispatch=True)
        assert m['result'] is True
        m['finalize'].assert_not_called()
        assert m['recut'].call_args.kwargs['run_stats']['failover'] == {
            'llm': ['primary'], 'whisper': False}

    def test_failover_not_reported_when_trigger_predates_run(self):
        # A slot active before this run started but absent from the frozen
        # snapshot was never actually routed to by this run.
        state = {'llm:primary': {'active': True, 'source': 'auto',
                                 'since': '2000-01-01T00:00:00Z', 'reason': 'x'}}
        m = _run_pipeline(_row(), route_snapshot={}, failover_state=state)
        assert m['result'] is True
        assert 'failover' not in m['finalize'].call_args.kwargs['run_stats']

    def test_failover_reported_when_triggered_mid_run(self):
        state = {'llm:primary': {'active': True, 'source': 'probe',
                                 'since': '2999-01-01T00:00:00Z', 'reason': 'x'}}
        snapshot = {'detection': {'provider_key': 'anthropic', 'credential_slot': 'primary'}}
        m = _run_pipeline(_row(), route_snapshot=snapshot, failover_state=state, standby_dispatch=True)
        assert m['result'] is True
        assert m['finalize'].call_args.kwargs['run_stats']['failover'] == {
            'llm': ['primary'], 'whisper': False}

    def test_failover_not_reported_for_slot_absent_from_snapshot(self):
        # #806: a snapshot that only ever routed to primary must not report
        # secondary, even if secondary's own failover state changed mid-run.
        snapshot = {'detection': {'provider_key': 'openai-compatible',
                                  'credential_slot': 'primary'}}
        state = {'llm:secondary': {'active': True, 'source': 'probe',
                                   'since': '2999-01-01T00:00:00Z', 'reason': 'x'}}
        m = _run_pipeline(_row(), route_snapshot=snapshot, failover_state=state)
        assert m['result'] is True
        assert 'failover' not in m['finalize'].call_args.kwargs['run_stats']

    def test_provider_denial_happens_after_transcription_before_detection(self):
        m = _run_pipeline(
            _row(), admission={'allowed': False, 'reason': 'daily limit'})
        assert m['result'] is False
        m['dat'].assert_called_once()
        m['detect'].assert_not_called()
        m['db'].release_provider_spend.assert_not_called()
        m['db'].reconcile_provider_spend.assert_not_called()

    def test_download_failure_creates_no_provider_reservation(self):
        m = _run_pipeline(_row(), download_error=RuntimeError('download failed'))
        assert m['result'] is False
        m['db'].reserve_provider_spend.assert_not_called()
        m['db'].release_provider_spend.assert_not_called()
        m['db'].reconcile_provider_spend.assert_not_called()

    def test_provider_reservation_uncertain_after_attempt(self):
        m = _run_pipeline(_row(), detect_error=RuntimeError('provider failed'))
        assert m['result'] is False
        m['db'].reconcile_provider_spend.assert_called_once_with(
            'provider-run-1', None)
        m['db'].release_provider_spend.assert_not_called()

    def test_provider_reservation_reconciles_actual_cost(self):
        ctx = run_context.begin('mode-feed', 'ep1')
        try:
            m = _run_pipeline(_row(), real_token_tracking=True)
            assert m['result'] is True
            m['db'].reconcile_provider_spend.assert_called_once_with(
                'provider-run-1', 12_000)
            m['db'].release_provider_spend.assert_not_called()
        finally:
            run_context.end(ctx)

    def test_approval_recut_reconciles_actual_cost(self):
        ctx = run_context.begin('mode-feed', 'ep1')
        try:
            m = _run_pipeline(
                _row(), real_token_tracking=True, approval_recut=True)
            assert m['result'] is True
            m['recut'].assert_called_once()
            m['finalize'].assert_not_called()
            m['db'].reconcile_provider_spend.assert_called_once_with(
                'provider-run-1', 12_000)
        finally:
            run_context.end(ctx)


INVERTED = [{'start': 0.0, 'end': 60.0, 'confidence': 0.9,
             'reason': 'keep-content inversion', 'sponsor': None,
             'detection_stage': 'keep_content'}]


def _make_detector(db_mode=None):
    d = AdDetector(api_key='test-key')
    # Stub db so _ensure_deps keeps it (the `is not None` guard) and no
    # on-disk Database is built.
    d.db = MagicMock()
    d.db.get_podcast_detection_mode.return_value = db_mode
    d.db.get_false_positive_corrections.return_value = []
    d.db.get_podcast_false_positive_texts.return_value = []
    return d


def _run_transcript(d, keep_content, kc_return):
    with ExitStack() as stack:
        stack.enter_context(patch.object(d, 'initialize_client'))
        stack.enter_context(patch.object(d, 'get_model', return_value='m'))
        kc = stack.enter_context(patch.object(
            d, '_detect_keep_content_ads', return_value=kc_return))
        blk = stack.enter_context(patch.object(
            d, 'detect_ads', return_value={'ads': [], 'status': 'success'}))
        stack.enter_context(
            patch('llm_route.llm_client.get_llm_timeout', return_value=30))
        stack.enter_context(
            patch('llm_route.llm_client.get_llm_max_retries', return_value=1))
        result = d.process_transcript(
            SEGMENTS, 'Pod', 'Ep', 'slug', 'ep1', keep_content=keep_content)
    return result, kc, blk


class TestProcessTranscriptKeepContentParam:
    def test_true_runs_keep_content_without_db_read(self):
        d = _make_detector()
        result, kc, blk = _run_transcript(d, True, list(INVERTED))
        kc.assert_called_once()
        blk.assert_not_called()
        d.db.get_podcast_detection_mode.assert_not_called()
        assert len(result['ads']) == 1
        assert result['ads'][0]['detection_stage'] == 'keep_content'

    def test_false_runs_blacklist_without_db_read(self):
        d = _make_detector(db_mode=DETECTION_MODE_KEEP_CONTENT)
        result, kc, blk = _run_transcript(d, False, list(INVERTED))
        kc.assert_not_called()
        blk.assert_called_once()
        # Even a DB row set to keep_content is ignored when the orchestrator
        # already resolved the mode (skip/passthrough precedence upstream).
        d.db.get_podcast_detection_mode.assert_not_called()

    def test_none_resolves_keep_content_from_db(self):
        # Backward-compat default: callers outside the pipeline (e.g. the
        # retry-detection API) keep the per-call DB resolution.
        d = _make_detector(db_mode=DETECTION_MODE_KEEP_CONTENT)
        result, kc, blk = _run_transcript(d, None, list(INVERTED))
        kc.assert_called_once()
        blk.assert_not_called()
        d.db.get_podcast_detection_mode.assert_called_once_with('slug')

    def test_none_defaults_to_blacklist_from_db(self):
        d = _make_detector(db_mode=None)
        result, kc, blk = _run_transcript(d, None, list(INVERTED))
        kc.assert_not_called()
        blk.assert_called_once()

    def test_gate_failure_falls_back_to_blacklist(self):
        # _detect_keep_content_ads returning None (safety gates tripped) must
        # still fall through to normal detection, exactly as before.
        d = _make_detector()
        result, kc, blk = _run_transcript(d, True, None)
        kc.assert_called_once()
        blk.assert_called_once()


class TestProcessTranscriptSkipLlm:
    def test_skip_llm_never_calls_stage3(self):
        d = _make_detector()
        with ExitStack() as stack:
            stack.enter_context(patch.object(d, 'initialize_client'))
            kc = stack.enter_context(patch.object(d, '_detect_keep_content_ads'))
            blk = stack.enter_context(patch.object(d, 'detect_ads'))
            result = d.process_transcript(
                SEGMENTS, 'Pod', 'Ep', 'slug', 'ep1', skip_llm=True)
        kc.assert_not_called()
        blk.assert_not_called()
        assert result['status'] == 'llm_skipped'
        assert result['detection_stats']['claude_matches'] == 0

    def test_skip_llm_tolerates_empty_segments(self):
        d = _make_detector()
        with ExitStack() as stack:
            stack.enter_context(patch.object(d, 'initialize_client'))
            result = d.process_transcript(
                [], 'Pod', 'Ep', 'slug', 'ep1', skip_llm=True)
        assert result['status'] == 'llm_skipped'
        assert result['ads'] == []


class TestNormalizeProcessingMode:
    @pytest.mark.parametrize('value,expected', [
        (PROCESSING_MODE_PASSTHROUGH,
         {'passthrough_enabled': 1, 'skip_ad_detection': 0, 'detection_mode': None}),
        (PROCESSING_MODE_SKIP_DETECTION,
         {'passthrough_enabled': 0, 'skip_ad_detection': 1, 'detection_mode': None}),
        (PROCESSING_MODE_KEEP_CONTENT,
         {'passthrough_enabled': 0, 'skip_ad_detection': 0,
          'detection_mode': DETECTION_MODE_KEEP_CONTENT}),
        (PROCESSING_MODE_STANDARD,
         {'passthrough_enabled': 0, 'skip_ad_detection': 0, 'detection_mode': None}),
    ])
    def test_canonical_encoding(self, value, expected):
        updates, err = _normalize_processing_mode(value)
        assert err is None
        assert updates == expected

    @pytest.mark.parametrize('value', [
        PROCESSING_MODE_PASSTHROUGH, PROCESSING_MODE_SKIP_DETECTION,
        PROCESSING_MODE_KEEP_CONTENT, PROCESSING_MODE_STANDARD,
        PROCESSING_MODE_CUE_ONLY,
    ])
    def test_round_trip_through_resolver(self, value):
        updates, _ = _normalize_processing_mode(value)
        assert resolve_feed_processing_mode(updates) == value

    @pytest.mark.parametrize('bad', ['PASSTHROUGH', 42, [], {}])
    def test_invalid_values_rejected(self, bad):
        updates, err = _normalize_processing_mode(bad)
        assert updates is None
        assert 'processingMode must be one of' in err

    def test_none_and_empty_mean_standard(self):
        for v in (None, ''):
            updates, err = _normalize_processing_mode(v)
            assert err is None
            assert resolve_feed_processing_mode(updates) == PROCESSING_MODE_STANDARD

    def test_detection_mode_cue_only_rejected(self):
        value, err = _normalize_detection_mode('cue_only')
        assert value is None
        assert 'detectionMode must be one of' in err


class TestCueOnlyResolution:
    def test_detection_mode_cue_only_resolves(self):
        row = {'passthrough_enabled': None, 'skip_ad_detection': None,
               'detection_mode': DETECTION_MODE_CUE_ONLY}
        assert resolve_feed_processing_mode(row) == PROCESSING_MODE_CUE_ONLY

    def test_passthrough_and_skip_still_shadow_cue_only(self):
        base = {'detection_mode': DETECTION_MODE_CUE_ONLY}
        assert resolve_feed_processing_mode(
            {**base, 'passthrough_enabled': 1}) == PROCESSING_MODE_PASSTHROUGH
        assert resolve_feed_processing_mode(
            {**base, 'skip_ad_detection': 1}) == PROCESSING_MODE_SKIP_DETECTION

    def test_cue_only_round_trips_through_encoder(self):
        from config import PROCESSING_MODE_COLUMN_UPDATES
        updates = PROCESSING_MODE_COLUMN_UPDATES[PROCESSING_MODE_CUE_ONLY]
        assert resolve_feed_processing_mode(updates) == PROCESSING_MODE_CUE_ONLY

    def test_resolve_skip_transcription(self):
        assert resolve_skip_transcription({'skip_transcription': 1}) is True
        assert resolve_skip_transcription({'skip_transcription': 0}) is False
        assert resolve_skip_transcription({}) is False
        assert resolve_skip_transcription(None) is False

    def test_resolve_cue_only_safety_default_and_values(self):
        assert resolve_cue_only_safety(None) == CUE_ONLY_SAFETY_HOLD_NEW
        assert resolve_cue_only_safety({}) == CUE_ONLY_SAFETY_HOLD_NEW
        assert resolve_cue_only_safety(
            {'cue_only_safety': 'auto_cut'}) == CUE_ONLY_SAFETY_AUTO_CUT
        assert resolve_cue_only_safety(
            {'cue_only_safety': 'bogus'}) == CUE_ONLY_SAFETY_HOLD_NEW


class TestCueOnlyPipelineWiring:
    def test_download_and_transcribe_skip_returns_empty_segments(self):
        with patch.object(transcribe, '_download_episode_audio', return_value='/tmp/a.mp3'), \
             patch.object(processing.storage, 'get_original_path', return_value=None), \
             patch.object(processing.transcriber, 'transcribe_chunked') as tr:
            path, segments = transcribe._download_and_transcribe(
                'slug', 'ep1', 'http://example.com/e.mp3', skip_transcription=True)
        tr.assert_not_called()
        assert path == '/tmp/a.mp3'
        assert segments == []

    def test_download_and_transcribe_skip_reuses_retained_original(self):
        with patch.object(transcribe, '_download_episode_audio') as dl, \
             patch.object(processing.storage, 'get_original_path', return_value='/tmp/orig.mp3'), \
             patch.object(processing.os.path, 'exists', return_value=True), \
             patch.object(common, '_copy_retained_original_to_temp',
                          return_value='/tmp/copy.mp3') as copy, \
             patch.object(processing.transcriber, 'transcribe_chunked') as tr:
            path, segments = transcribe._download_and_transcribe(
                'slug', 'ep1', 'http://example.com/e.mp3', skip_transcription=True)
        dl.assert_not_called()
        copy.assert_called_once_with('/tmp/orig.mp3')
        tr.assert_not_called()
        assert path == '/tmp/copy.mp3'
        assert segments == []

    def test_run_stats_to_api_carries_cue_only_flags(self):
        from episode_runs import run_stats_to_api
        out = run_stats_to_api({'mode': 'auto', 'cue_only': True,
                                 'transcription_skipped': True})
        assert out['cueOnly'] is True
        assert out['transcriptionSkipped'] is True

    def test_cue_only_mode_wires_detection_and_analysis(self):
        m = _run_pipeline(_row(mode=DETECTION_MODE_CUE_ONLY))
        assert m['result'] is True
        assert m['analyze'].call_args.kwargs['force_cue_detection'] is True
        detect_kwargs = m['detect'].call_args.kwargs
        assert detect_kwargs['skip_llm'] is True
        assert detect_kwargs['force_create_from_pairs'] is True
        assert detect_kwargs['strict_pair_roles'] is True
        assert detect_kwargs['episode_duration'] == 100.0
        assert m['verify'].call_args.kwargs['skip_verification'] is True
        assert m['refine'].call_args.kwargs['cue_only_safety'] == CUE_ONLY_SAFETY_HOLD_NEW
        assert m['refine'].call_args.kwargs['cue_unproven_template_ids'] == set()
        assert m['refine'].call_args.kwargs['apply_heuristic_rolls'] is False
        run_stats = m['finalize'].call_args.kwargs['run_stats']
        assert run_stats['cue_only'] is True
        assert run_stats['verification_skipped'] is True
        assert 'transcription_skipped' not in run_stats

    def test_standard_mode_wires_apply_heuristic_rolls_true(self):
        m = _run_pipeline(_row())
        assert m['result'] is True
        assert m['refine'].call_args.kwargs['apply_heuristic_rolls'] is True

    def test_cue_only_safety_hold_new_collects_unproven_template_ids(self):
        row = dict(_row(mode=DETECTION_MODE_CUE_ONLY), cue_only_safety='hold_new')
        m = _run_pipeline(
            row, cue_template_counts={1: 1},
            cue_templates=[{'id': 1, 'enabled': 1},
                           {'id': 2, 'enabled': 1},
                           {'id': 3, 'enabled': 0}])
        assert m['result'] is True
        unproven = m['refine'].call_args.kwargs['cue_unproven_template_ids']
        # id 1 has 1 paired episode (< CUE_ONLY_PROVEN_EPISODES): unproven.
        # id 2 has no recorded pairs: unproven. id 3 is disabled: excluded.
        assert unproven == {1, 2}

    def test_cue_only_safety_auto_cut_skips_template_lookup(self):
        row = dict(_row(mode=DETECTION_MODE_CUE_ONLY), cue_only_safety='auto_cut')
        m = _run_pipeline(row)
        assert m['result'] is True
        # The unproven-ids lookup (hold_new only) is skipped; the quiet-template
        # drift check calls list_cue_templates_for_feed_ui regardless of safety mode.
        m['db'].cue_template_paired_episode_counts.assert_not_called()
        assert m['refine'].call_args.kwargs['cue_only_safety'] == CUE_ONLY_SAFETY_AUTO_CUT
        assert m['refine'].call_args.kwargs['cue_unproven_template_ids'] == set()

    def test_cue_only_with_skip_transcription_records_stat_and_threads_flag(self):
        row = dict(_row(mode=DETECTION_MODE_CUE_ONLY), skip_transcription=1)
        m = _run_pipeline(row)
        assert m['result'] is True
        assert m['dat'].call_args.kwargs['skip_transcription'] is True
        run_stats = m['finalize'].call_args.kwargs['run_stats']
        assert run_stats['transcription_skipped'] is True
        assert run_stats['cue_only'] is True

    def test_standard_mode_never_sets_skip_transcription(self):
        m = _run_pipeline(_row())
        assert m['dat'].call_args.kwargs['skip_transcription'] is False

    def test_cue_only_skips_ad_reviewer_even_when_enabled(self):
        # The mode promises zero LLM calls, so the guard must bypass
        # _run_ad_reviewer regardless of the enable_ad_review setting.
        m = _run_pipeline(_row(mode=DETECTION_MODE_CUE_ONLY), enable_ad_review=True)
        assert m['result'] is True
        m['reviewer'].assert_not_called()

    def test_standard_mode_still_invokes_ad_reviewer_when_enabled(self):
        m = _run_pipeline(_row(), enable_ad_review=True)
        assert m['result'] is True
        m['reviewer'].assert_called_once()


class TestRefineAndValidateHeuristicRollGating:
    """apply_heuristic_rolls=False (cue_only) skips regex pre/post-roll and VAD-gap synthesis.

    Those markers carry no cue or pattern-DB evidence. Uses an empty all_ads so the validator is never reached.
    """

    def _call(self, **kwargs):
        with patch.object(detect, '_apply_heuristic_rolls') as rolls, \
             patch.object(processing, 'db'):
            result = detect._refine_and_validate(
                'slug', 'ep1', [], SEGMENTS, '/tmp/a.mp3',
                'desc', 100.0, 0.8, 'Pod', corrections=([], []), **kwargs)
        return result, rolls

    def test_cue_only_never_calls_apply_heuristic_rolls(self):
        result, rolls = self._call(apply_heuristic_rolls=False)
        rolls.assert_not_called()
        assert result == ([], [])

    def test_standard_mode_calls_apply_heuristic_rolls(self):
        result, rolls = self._call()
        rolls.assert_called_once()
        assert result == ([], [])
