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
    resolve_feed_processing_mode,
)
from ad_detector import AdDetector
import main_app.processing as processing

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


def _run_pipeline(podcast_row):
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
        p(processing, '_download_and_transcribe',
          return_value=('/tmp/mode.mp3', SEGMENTS))
        p(processing, '_run_differential_fetch', return_value=None)
        p(processing, '_run_audio_analysis', return_value=None)
        p(processing, 'load_positional_prior', return_value=None)
        detect = p(processing, '_detect_ads_first_pass', return_value=([], 0, None))
        p(processing, '_refine_and_validate', return_value=([], []))
        p(processing, '_run_ad_reviewer', return_value=([], []))
        p(processing, '_snap_terminal_starts', return_value=[])
        p(processing, '_complete_cut_tails', return_value=[])
        local_ap_cls = p(processing, 'AudioProcessor')
        verify = p(processing, '_run_verification_pass',
                   return_value=(0, [], [], [], '/tmp/cut.mp3', 0, True))
        p(processing, '_generate_assets')
        p(processing, '_finalize_episode')
        p(processing.shutil, 'move')
        p(processing.os, 'unlink')
        p(processing.os.path, 'exists', return_value=False)

        db.get_episode.return_value = {}
        db.get_podcast_by_slug.return_value = podcast_row
        db.get_setting.return_value = 'false'
        db.get_all_settings.return_value = {}
        audio_processor.get_audio_duration.return_value = 100.0
        local_ap = local_ap_cls.return_value
        local_ap.process_episode.return_value = ('/tmp/cut.mp3', [])
        local_ap.get_audio_duration.return_value = 100.0
        storage.get_episode_path.return_value = '/tmp/final.mp3'
        result = processing.process_episode(
            'mode-feed', 'ep1', 'https://example.com/ep1.mp3')
    return {'result': result, 'detect': detect, 'verify': verify}


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
        assert m['verify'].call_args.kwargs['skip_detection'] is True

    # The pipeline passes keep_content=None so the detector resolves the
    # mode from the DB at detection time -- a detection_mode toggle made
    # during the minutes-long download/transcription window must be honored
    # (the detector-side resolution is covered by
    # TestProcessTranscriptKeepContentParam below).
    def test_keep_content_mode_defers_resolution_to_detector(self):
        m = _run_pipeline(_row(mode=DETECTION_MODE_KEEP_CONTENT))
        assert m['result'] is True
        assert m['detect'].call_args.kwargs['keep_content'] is None
        assert m['verify'].call_args.kwargs['skip_detection'] is False

    def test_standard_mode_defers_resolution_to_detector(self):
        m = _run_pipeline(_row())
        assert m['result'] is True
        assert m['detect'].call_args.kwargs['keep_content'] is None
        assert m['verify'].call_args.kwargs['skip_detection'] is False


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
