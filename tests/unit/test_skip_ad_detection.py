"""Tests for issue #538: per-feed skip-ad-detection mode.

A feed with skip_ad_detection set still gets transcription, chapters, and
a transcript, but the detection stages (cross-fetch differential, audio
analysis, first-pass detection, verification pass) are skipped and nothing
is cut.
"""
import os
import sys
import tempfile
from contextlib import ExitStack

os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='skipdet_test_'))
os.environ.setdefault('SECRET_KEY', 'test-secret')

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from unittest.mock import patch

import main_app.processing as processing

SEGMENTS = [{'start': 0.0, 'end': 5.0, 'text': 'hello'},
            {'start': 5.0, 'end': 10.0, 'text': 'world'}]


def _run_pipeline(skip_ad_detection):
    podcast_row = {'id': 1, 'slug': 'skip-feed', 'description': None,
                   'tags': None, 'dai_platform': None,
                   'passthrough_enabled': None,
                   'skip_ad_detection': skip_ad_detection}
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
          return_value=('/tmp/skip.mp3', SEGMENTS))
        differential = p(processing, '_run_differential_fetch', return_value=None)
        audio_analysis = p(processing, '_run_audio_analysis', return_value=None)
        prior = p(processing, 'load_positional_prior', return_value=None)
        detect = p(processing, '_detect_ads_first_pass', return_value=([], 0, None))
        refine = p(processing, '_refine_and_validate', return_value=([], []))
        p(processing, '_run_ad_reviewer', return_value=([], []))
        p(processing, '_snap_terminal_starts', return_value=[])
        p(processing, '_complete_cut_tails', return_value=[])
        local_ap_cls = p(processing, 'AudioProcessor')
        verify = p(processing, '_run_verification_pass',
                   return_value=(0, [], [], [], '/tmp/cut.mp3', 0, True))
        generate_assets = p(processing, '_generate_assets')
        finalize = p(processing, '_finalize_episode')
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
            'skip-feed', 'ep1', 'https://example.com/ep1.mp3')
    return {'result': result, 'db': db, 'differential': differential,
            'audio_analysis': audio_analysis, 'prior': prior, 'detect': detect,
            'refine': refine, 'verify': verify,
            'generate_assets': generate_assets, 'finalize': finalize}


class TestSkipAdDetection:
    def test_detection_stages_skipped_but_assets_generated(self):
        m = _run_pipeline(skip_ad_detection=1)

        assert m['result'] is True
        m['differential'].assert_not_called()
        m['audio_analysis'].assert_not_called()
        m['prior'].assert_not_called()
        m['detect'].assert_not_called()
        # Stage 4 must not run either: its heuristic pre/post-roll pass adds
        # cuts even to an empty ad list.
        m['refine'].assert_not_called()
        # The verification stage owns its skip: the flag is forwarded and the
        # stage early-returns (covered by test_verification_pass_early_return).
        assert m['verify'].call_args.kwargs['skip_detection'] is True
        m['generate_assets'].assert_called_once()
        # Stale markers from an earlier detection run describe cut audio and
        # must not survive next to the uncut file (mirrors pass-through).
        m['db'].clear_episode_ad_data.assert_called_once_with('skip-feed', 'ep1')
        kwargs = m['finalize'].call_args.kwargs
        run_stats = kwargs['run_stats']
        assert run_stats['detection_skipped'] is True
        assert run_stats['markers'] == {'cut': 0, 'held': 0, 'not_cut': 0}
        # Stats for stages that never ran are absent, not zero: zeros would
        # be indistinguishable from a detection run that found nothing.
        assert 'stage_hits' not in run_stats
        assert 'detected' not in run_stats
        assert 'verification_ads_cut' not in run_stats

    def test_flag_off_runs_detection_stages(self):
        m = _run_pipeline(skip_ad_detection=None)

        assert m['result'] is True
        m['differential'].assert_called_once()
        m['audio_analysis'].assert_called_once()
        m['detect'].assert_called_once()
        m['refine'].assert_called_once()
        m['verify'].assert_called_once()
        assert m['verify'].call_args.kwargs['skip_detection'] is False
        m['generate_assets'].assert_called_once()
        assert 'detection_skipped' not in m['finalize'].call_args.kwargs['run_stats']

    def test_verification_pass_early_return(self):
        # skip_detection short-circuits before any transcription or LLM work;
        # processed_path passes through untouched and the pass reports ok.
        result = processing._run_verification_pass(
            None, '/tmp/skip-cut.mp3', [], False, 0.8, None, None,
            skip_detection=True)
        assert result == (0, [], [], [], '/tmp/skip-cut.mp3', 0, True)
