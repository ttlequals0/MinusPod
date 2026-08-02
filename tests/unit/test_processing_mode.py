"""Per-feed processing mode resolution and plumbing.

The three per-feed toggles are deliberately independent DB columns
(issue #537): passthrough_enabled, skip_ad_detection, and
detection_mode='keep_content'. resolve_feed_processing_mode collapses them
to one effective mode with the precedence the pipeline has always had by
branch ordering: passthrough returned before the skip check ran, and a
skipped detection stage never consulted detection_mode. The truth table
below is that pre-centralization behavior, verbatim.
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
    resolve_skip_transcription,
    resolve_cue_only_safety,
)
from ad_detector import AdDetector
import main_app.processing as processing
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


def _run_pipeline(podcast_row, cue_template_counts=None, cue_templates=None,
                   enable_ad_review=False):
    """Drive process_episode with all stages stubbed (mirrors
    test_skip_ad_detection's harness) and return the interesting mocks."""
    with ExitStack() as stack:
        p = lambda *a, **k: stack.enter_context(patch.object(*a, **k))
        db = p(processing, 'db')
        p(processing, 'status_service')
        storage = p(processing, 'storage')
        audio_processor = p(processing, 'audio_processor')
        p(processing, 'start_episode_token_tracking')
        p(processing, 'get_available_memory_gb', return_value=None)
        p(processing, 'get_min_cut_confidence', return_value=0.8)
        dat = p(processing, '_download_and_transcribe',
                return_value=('/tmp/mode.mp3', SEGMENTS))
        p(processing, '_run_differential_fetch', return_value=None)
        analyze = p(processing, '_run_audio_analysis', return_value=None)
        p(processing, 'load_positional_prior', return_value=None)
        detect = p(processing, '_detect_ads_first_pass', return_value=([], 0, None))
        refine = p(processing, '_refine_and_validate', return_value=([], []))
        reviewer = p(processing, '_run_ad_reviewer', return_value=([], []))
        p(processing, '_snap_terminal_starts', return_value=[])
        p(processing, '_complete_cut_tails', return_value=[])
        local_ap_cls = p(processing, 'AudioProcessor')
        verify = p(processing, '_run_verification_pass',
                   return_value=(0, [], [], [], '/tmp/cut.mp3', 0, True, 0))
        p(processing, '_generate_assets')
        finalize = p(processing, '_finalize_episode')
        p(processing.shutil, 'move')
        p(processing.os, 'unlink')
        p(processing.os.path, 'exists', return_value=False)

        db.get_episode.return_value = {}
        db.get_podcast_by_slug.return_value = podcast_row
        if enable_ad_review:
            db.get_setting.side_effect = (
                lambda key, *a, **k: 'true' if key == 'enable_ad_review' else 'false')
        else:
            db.get_setting.return_value = 'false'
        db.get_all_settings.return_value = {}
        db.cue_template_paired_episode_counts.return_value = cue_template_counts or {}
        db.list_cue_templates_for_feed_ui.return_value = cue_templates or []
        db.cue_template_recent_activity.return_value = []
        audio_processor.get_audio_duration.return_value = 100.0
        local_ap = local_ap_cls.return_value
        local_ap.process_episode.return_value = ('/tmp/cut.mp3', [])
        local_ap.get_audio_duration.return_value = 100.0
        storage.get_episode_path.return_value = '/tmp/final.mp3'
        result = processing.process_episode(
            'mode-feed', 'ep1', 'https://example.com/ep1.mp3')
    return {'result': result, 'detect': detect, 'verify': verify,
            'analyze': analyze, 'refine': refine, 'finalize': finalize,
            'dat': dat, 'db': db, 'reviewer': reviewer}


def _row(pt=None, skip=None, mode=None):
    return {'id': 1, 'slug': 'mode-feed', 'description': None,
            'tags': None, 'dai_platform': None,
            'passthrough_enabled': pt, 'skip_ad_detection': skip,
            'detection_mode': mode}


class TestProcessEpisodeModePlumbing:
    def test_passthrough_wins_over_skip_and_keep_content(self):
        with patch.object(processing, 'db') as db, \
             patch.object(processing, '_passthrough_episode') as pt, \
             patch.object(processing, 'start_episode_token_tracking'):
            db.get_episode.return_value = {}
            db.get_podcast_by_slug.return_value = _row(
                pt=1, skip=1, mode=DETECTION_MODE_KEEP_CONTENT)
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

    # The pipeline passes keep_content=None so the detector resolves the
    # mode from the DB at detection time -- a detection_mode toggle made
    # during the minutes-long download/transcription window must be honored
    # (the detector-side resolution is covered by
    # TestProcessTranscriptKeepContentParam below).
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
            patch('ad_detector.get_llm_timeout', return_value=30))
        stack.enter_context(
            patch('ad_detector.get_llm_max_retries', return_value=1))
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
        with patch.object(processing, '_download_episode_audio', return_value='/tmp/a.mp3'), \
             patch.object(processing.storage, 'get_original_path', return_value=None), \
             patch.object(processing.transcriber, 'transcribe_chunked') as tr:
            path, segments = processing._download_and_transcribe(
                'slug', 'ep1', 'http://example.com/e.mp3', 'Pod', skip_transcription=True)
        tr.assert_not_called()
        assert path == '/tmp/a.mp3'
        assert segments == []

    def test_download_and_transcribe_skip_reuses_retained_original(self):
        with patch.object(processing, '_download_episode_audio') as dl, \
             patch.object(processing.storage, 'get_original_path', return_value='/tmp/orig.mp3'), \
             patch.object(processing.os.path, 'exists', return_value=True), \
             patch.object(processing, '_copy_retained_original_to_temp',
                          return_value='/tmp/copy.mp3') as copy, \
             patch.object(processing.transcriber, 'transcribe_chunked') as tr:
            path, segments = processing._download_and_transcribe(
                'slug', 'ep1', 'http://example.com/e.mp3', 'Pod', skip_transcription=True)
        dl.assert_not_called()
        copy.assert_called_once_with('/tmp/orig.mp3')
        tr.assert_not_called()
        assert path == '/tmp/copy.mp3'
        assert segments == []

    def test_run_stats_to_api_carries_cue_only_flags(self):
        from api.episodes import _run_stats_to_api
        out = _run_stats_to_api({'mode': 'auto', 'cue_only': True,
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
    """apply_heuristic_rolls=False (cue_only) must skip regex pre/post-roll
    and VAD-gap synthesis entirely, since those markers carry no cue or
    pattern-DB evidence. Calls _refine_and_validate directly with an empty
    all_ads list so the function returns before touching the validator."""

    def _call(self, **kwargs):
        with patch.object(processing, '_apply_heuristic_rolls') as rolls, \
             patch.object(processing, 'db') as db:
            db.get_false_positive_corrections.return_value = []
            db.get_confirmed_corrections.return_value = []
            result = processing._refine_and_validate(
                'slug', 'ep1', [], SEGMENTS, '/tmp/a.mp3',
                'desc', 100.0, 0.8, 'Pod', **kwargs)
        return result, rolls

    def test_cue_only_never_calls_apply_heuristic_rolls(self):
        result, rolls = self._call(apply_heuristic_rolls=False)
        rolls.assert_not_called()
        assert result == ([], [])

    def test_standard_mode_calls_apply_heuristic_rolls(self):
        result, rolls = self._call()
        rolls.assert_called_once()
        assert result == ([], [])
