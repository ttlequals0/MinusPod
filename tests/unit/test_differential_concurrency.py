"""Stage 1b differential fetch runs on a worker thread, overlapped with
stage 2 audio analysis, and its result/exception semantics survive the
thread boundary (join happens before the result is consumed)."""
import os
import sys
import tempfile
import threading
from contextlib import ExitStack

os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='diffconc_test_'))
os.environ.setdefault('SECRET_KEY', 'test-secret')

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from unittest.mock import patch

import main_app.processing as processing

SEGMENTS = [{'start': 0.0, 'end': 5.0, 'text': 'hello'},
            {'start': 5.0, 'end': 10.0, 'text': 'world'}]

DIFF_RESULT = {'status': 'ok',
               'regions': [{'start_s': 10.0, 'end_s': 20.0,
                            'kind': 'differential', 'corr': 0.0}],
               'refetch_meta': {}, 'error': None}


def _run_pipeline(differential_fn, audio_analysis_fn):
    podcast_row = {'id': 1, 'slug': 'diff-feed', 'description': None,
                   'tags': None, 'dai_platform': None,
                   'passthrough_enabled': None, 'skip_ad_detection': None}
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
          return_value=('/tmp/diff.mp3', SEGMENTS))
        p(processing, '_run_differential_fetch', side_effect=differential_fn)
        p(processing, '_run_audio_analysis', side_effect=audio_analysis_fn)
        p(processing, 'load_positional_prior', return_value=None)
        detect = p(processing, '_detect_ads_first_pass', return_value=([], 0, None))
        p(processing, '_refine_and_validate', return_value=([], []))
        p(processing, '_run_ad_reviewer', return_value=([], []))
        p(processing, '_snap_terminal_starts', return_value=[])
        p(processing, '_complete_cut_tails', return_value=[])
        local_ap_cls = p(processing, 'AudioProcessor')
        p(processing, '_run_verification_pass',
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
            'diff-feed', 'ep1', 'https://example.com/ep1.mp3')
    return result, detect


class TestDifferentialConcurrency:
    def test_fetch_overlaps_analysis_and_result_reaches_detection(self):
        analysis_started = threading.Event()
        seen = {}

        def differential_fn(*args, **kwargs):
            seen['thread'] = threading.current_thread()
            # Analysis (main thread) must be able to start while the fetch is
            # still in flight; serial execution would time out here.
            seen['overlapped'] = analysis_started.wait(timeout=10)
            return DIFF_RESULT

        def audio_analysis_fn(*args, **kwargs):
            analysis_started.set()
            return None

        result, detect = _run_pipeline(differential_fn, audio_analysis_fn)

        assert result is True
        assert seen['thread'] is not threading.main_thread()
        assert seen['overlapped'] is True
        # Joined result flows into first-pass detection.
        assert detect.call_args.kwargs['dai_differential'] == DIFF_RESULT

    def test_worker_exception_fails_episode_like_serial_call(self):
        def differential_fn(*args, **kwargs):
            raise RuntimeError('boom in worker')

        result, detect = _run_pipeline(differential_fn, lambda *a, **k: None)

        assert result is False
        detect.assert_not_called()
