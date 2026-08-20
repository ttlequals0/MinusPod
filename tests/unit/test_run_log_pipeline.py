"""Pipeline wiring for episode run logs (#660): a run leaves a finalized
JSONL file keyed to its processing_history row, or none when the feed opts
out."""
import json
import logging
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from tests.app_bootstrap import bootstrap

_data_dir = bootstrap('run_log_pipeline_test_', reset_storage=True)

import main_app.processing as processing  # noqa: E402
import run_log  # noqa: E402
from ad_detector import AdDetector  # noqa: E402
from ad_reviewer import AdReviewer  # noqa: E402

SEGMENTS = [{'start': 0.0, 'end': 10.0, 'text': 'hello'}]
SLUG = 'run-log-feed'
EPISODE_ID = 'ep0000000001'


@pytest.fixture
def db():
    handle = processing.db
    handle.create_podcast(SLUG, 'https://example.com/feed.xml', 'Run Log Feed')
    yield handle
    handle.delete_podcast(SLUG)


def _run_pipeline(fail=False):
    """Drive process_episode with every heavy stage stubbed out."""
    with ExitStack() as stack:
        p = lambda *a, **k: stack.enter_context(patch.object(*a, **k))  # noqa: E731
        p(processing, 'status_service')
        p(processing, 'start_episode_token_tracking')
        p(processing, 'get_available_memory_gb', return_value=None)
        p(processing, 'get_min_cut_confidence', return_value=0.8)
        p(processing.ad_detector, 'get_model', return_value='test-model')
        p(processing.ad_detector, 'get_verification_model', return_value='test-model')
        if fail:
            p(processing, '_download_and_transcribe',
              side_effect=RuntimeError('transcription exploded'))
        else:
            p(processing, '_download_and_transcribe',
              return_value=('/tmp/run-log.mp3', SEGMENTS))
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
        p(processing, '_persist_episode_state')
        p(processing, '_refresh_rss_for_slug')
        p(processing, '_log_completion_summary',
          return_value={'input_tokens': 0, 'output_tokens': 0, 'cost': 0.0})
        p(processing, 'get_episode_token_totals',
          return_value={'input_tokens': 0, 'output_tokens': 0, 'cost': 0.0})
        p(processing, 'fire_event')
        p(processing, '_maybe_fire_low_ad_yield_action')
        p(processing, '_maybe_enqueue_degraded_redetect')
        p(processing.shutil, 'move')
        p(processing.os, 'unlink')
        p(processing.os.path, 'exists', return_value=False)
        p(processing.audio_processor, 'get_audio_duration', return_value=100.0)
        p(processing.storage, 'get_episode_path', return_value='/tmp/final.mp3')
        local_ap = local_ap_cls.return_value
        local_ap.process_episode.return_value = ('/tmp/cut.mp3', [])
        local_ap.get_audio_duration.return_value = 100.0
        return processing.process_episode(
            SLUG, EPISODE_ID, 'https://example.com/ep1.mp3',
            episode_title='Run Log Episode')


def _history_row(db):
    rows = db.get_episode_processing_runs(
        db.get_podcast_by_slug(SLUG)['id'], EPISODE_ID)
    assert len(rows) == 1
    return rows[0]


def _log_lines(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line]


class TestStorageEnabled:
    def test_a_completed_run_leaves_a_log_keyed_to_its_history_row(self, db):
        assert _run_pipeline() is True

        row = _history_row(db)
        expected = (f"logs/episodes/{SLUG}/{EPISODE_ID}/run-{row['id']}.jsonl")
        assert row['log_file'] == expected
        path = processing.storage.data_dir / expected
        assert path.exists()
        assert row['log_bytes'] == path.stat().st_size
        messages = [entry['msg'] for entry in _log_lines(path)]
        assert any('Starting' in msg for msg in messages)
        assert all(f"[{SLUG}:{EPISODE_ID}]" in msg for msg in messages)

    def test_a_failed_run_keeps_its_log(self, db):
        assert _run_pipeline(fail=True) is False

        row = _history_row(db)
        assert row['status'] == 'failed'
        assert row['log_file']
        assert (processing.storage.data_dir / row['log_file']).exists()

    def test_no_temp_files_are_left_behind(self, db):
        _run_pipeline()

        temp_dir = run_log.run_log_temp_dir(processing.storage.data_dir)
        assert list(temp_dir.glob('*.tmp')) == []

    def test_the_recorder_is_detached_when_the_run_ends(self, db):
        _run_pipeline()

        assert run_log.current_recorder() is None
        assert not [h for h in logging.getLogger().handlers
                    if isinstance(h, run_log.RunLogRecorder)]


class TestStorageDisabled:
    def test_a_feed_opted_out_stores_nothing(self, db):
        db.update_podcast(SLUG, episode_logs='off')

        assert _run_pipeline() is True

        row = _history_row(db)
        assert row['log_file'] is None
        assert row['log_bytes'] is None
        assert not (processing.storage.data_dir / run_log.run_log_relative_path(
            SLUG, EPISODE_ID, row['id'])).exists()

    def test_retention_zero_stores_nothing(self, db):
        db.set_setting('episode_log_retention_days', '0', is_default=False)
        try:
            assert _run_pipeline() is True
        finally:
            db.set_setting('episode_log_retention_days', '30', is_default=False)

        assert _history_row(db)['log_file'] is None


class TestWorkerThreadRegistration:
    def test_detection_windows_register_their_thread(self, tmp_path):
        recorder = run_log.RunLogRecorder('w-feed', 'w-ep', logging.INFO, tmp_path)
        worker_logger = logging.getLogger('podcast.test.windows')
        worker_logger.setLevel(logging.INFO)

        def worker(window_idx, window, total_windows):
            worker_logger.info('window %s has no run tag', window_idx)
            return SimpleNamespace(failed=False)

        recorder.attach()
        try:
            AdDetector._run_windows(
                None, [0, 1], max_workers=2, progress_callback=None,
                progress_base=0, progress_range=10, worker=worker)
        finally:
            recorder.detach()

        lines = _log_lines(recorder.temp_path)
        recorder.discard()
        assert len(lines) == 2

    def test_reviewer_batch_registers_its_thread(self, tmp_path):
        recorder = run_log.RunLogRecorder('w-feed', 'w-ep', logging.INFO, tmp_path)
        worker_logger = logging.getLogger('podcast.test.reviewer')
        worker_logger.setLevel(logging.INFO)
        reviewer = MagicMock()
        reviewer._review_single.side_effect = (
            lambda **kwargs: (worker_logger.info('reviewing without a tag'),
                              ('verdict', kwargs['ad']))[1])

        recorder.attach()
        try:
            AdReviewer._run_review_batch(
                reviewer, [{'start': 0}, {'start': 1}], pool='accepted',
                pass_num=1, segments=[], episode_meta={}, system_prompt='p',
                model='m', max_shift=5, max_workers=2)
        finally:
            recorder.detach()

        lines = _log_lines(recorder.temp_path)
        recorder.discard()
        assert len(lines) == 2


class TestPathHelpers:
    def test_run_log_path_matches_the_documented_layout(self, tmp_path):
        path = run_log.run_log_path(tmp_path, 'feed', 'ep1', 42)
        assert path == tmp_path / 'logs' / 'episodes' / 'feed' / 'ep1' / 'run-42.jsonl'
        assert run_log.run_log_relative_path('feed', 'ep1', 42) == (
            'logs/episodes/feed/ep1/run-42.jsonl')

    def test_traversal_in_the_slug_is_refused(self, tmp_path):
        from storage import PathContainmentError
        with pytest.raises(PathContainmentError):
            run_log.run_log_path(tmp_path, '../../etc', 'ep1', 1)


class TestHistoryPointer:
    def test_pointer_write_and_clear(self, db):
        podcast = db.get_podcast_by_slug(SLUG)
        history_id = db.record_processing_history(
            podcast_id=podcast['id'], podcast_slug=SLUG,
            podcast_title='Run Log Feed', episode_id=EPISODE_ID,
            episode_title='One', status='completed')

        assert db.set_history_log_pointer(history_id, 'logs/x.jsonl', 12) is True
        row = _history_row(db)
        assert (row['log_file'], row['log_bytes']) == ('logs/x.jsonl', 12)

        assert db.set_history_log_pointer(history_id, None, None) is True
        row = _history_row(db)
        assert (row['log_file'], row['log_bytes']) == (None, None)
