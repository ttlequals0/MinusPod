from contextlib import ExitStack
import os
import threading
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='timing_test_'))
os.environ.setdefault('SECRET_KEY', 'test-secret')

import main_app.processing as processing
import run_context
from utils import subprocess_registry


def test_run_timing_records_elapsed_time_when_stage_raises():
    clock = iter([10.0, 12.5])
    timing = run_context.RunTiming(clock=lambda: next(clock))

    try:
        with timing.measure('cut'):
            raise RuntimeError('cut failed')
    except RuntimeError:
        pass

    snapshot = timing.snapshot()
    assert 'cut' in snapshot
    assert snapshot['cut'] == 2.5


def test_run_timing_keeps_zero_length_measurement():
    timing = run_context.RunTiming(clock=lambda: 5.0)

    with timing.measure('detection'):
        pass

    assert timing.snapshot() == {'detection': 0.0}


def test_run_timing_accumulates_explicit_parallel_task_totals():
    timing = run_context.RunTiming()
    timing.add('ffmpeg', 1.25)
    timing.add('ffmpeg', 2.75)

    assert timing.snapshot() == {'ffmpeg': 4.0}


def test_run_timing_accumulates_parallel_stage_workers():
    clocks = {}
    clock_lock = threading.Lock()

    def monotonic():
        ident = threading.get_ident()
        with clock_lock:
            values = clocks.setdefault(ident, iter([10.0, 13.0]))
        return next(values)

    timing = run_context.RunTiming(clock=monotonic)
    barrier = threading.Barrier(2)

    def worker():
        with timing.measure('audio_analysis'):
            barrier.wait(timeout=2)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)
        assert not thread.is_alive()

    assert timing.snapshot()['audio_analysis'] == 6.0


def test_tracked_run_records_ffmpeg_time_on_failure(monkeypatch):
    class FakeProcess:
        returncode = 1

        def communicate(self, **kwargs):
            return b'', b'failed'

    ctx = run_context.begin('example-podcast', 'a1b2c3d4e5f6')
    clock = iter([10.0, 13.0])
    monkeypatch.setattr(subprocess_registry, 'time', SimpleNamespace(monotonic=lambda: next(clock)))
    monkeypatch.setattr(subprocess_registry.subprocess, 'Popen', lambda *args, **kwargs: FakeProcess())
    try:
        try:
            subprocess_registry.tracked_run(['ffmpeg', '-i', 'input.mp3'], check=True)
        except subprocess_registry.subprocess.CalledProcessError:
            pass
        assert ctx.timing.snapshot()['ffmpeg'] == 3.0
    finally:
        run_context.end(ctx)


def test_tracked_run_excludes_ffprobe_from_ffmpeg_total(monkeypatch):
    class FakeProcess:
        returncode = 0

        def communicate(self, **kwargs):
            return b'', b''

    ctx = run_context.begin('example-podcast', 'a1b2c3d4e5f6')
    monkeypatch.setattr(subprocess_registry.subprocess, 'Popen', lambda *args, **kwargs: FakeProcess())
    try:
        subprocess_registry.tracked_run(['ffprobe', '-i', 'input.mp3'])
        assert ctx.timing.snapshot() == {'ffmpeg': 0.0}
    finally:
        run_context.end(ctx)


def test_tracked_run_records_ffmpeg_time_when_communicate_times_out(monkeypatch):
    class FakeProcess:
        returncode = -9

        def communicate(self, **kwargs):
            if kwargs.get('timeout') is not None:
                raise subprocess_registry.subprocess.TimeoutExpired('ffmpeg', 1)
            return b'', b''

        def kill(self):
            return None

    ctx = run_context.begin('example-podcast', 'timeout')
    clock = iter([20.0, 24.5])
    monkeypatch.setattr(
        subprocess_registry, 'time',
        SimpleNamespace(monotonic=lambda: next(clock)),
    )
    monkeypatch.setattr(
        subprocess_registry.subprocess, 'Popen',
        lambda *args, **kwargs: FakeProcess(),
    )
    try:
        try:
            subprocess_registry.tracked_run(
                ['ffmpeg', '-i', 'input.mp3'], timeout=1,
            )
        except subprocess_registry.subprocess.TimeoutExpired:
            pass
        assert ctx.timing.snapshot()['ffmpeg'] == 4.5
    finally:
        run_context.end(ctx)


def test_tracked_run_accumulates_ffmpeg_retries(monkeypatch):
    class FakeProcess:
        returncode = 0

        def communicate(self, **kwargs):
            return b'', b''

    ctx = run_context.begin('example-podcast', 'retry')
    clock = iter([1.0, 2.0, 3.0, 5.0])
    monkeypatch.setattr(
        subprocess_registry, 'time',
        SimpleNamespace(monotonic=lambda: next(clock)),
    )
    monkeypatch.setattr(
        subprocess_registry.subprocess, 'Popen',
        lambda *args, **kwargs: FakeProcess(),
    )
    try:
        subprocess_registry.tracked_run(['/usr/bin/ffmpeg', '-i', 'input.mp3'])
        subprocess_registry.tracked_run(['ffmpeg', '-i', 'input.mp3'])
        assert ctx.timing.snapshot()['ffmpeg'] == 3.0
    finally:
        run_context.end(ctx)


def test_tracked_popen_does_not_count_an_unfinished_process(monkeypatch):
    class FakeProcess:
        def poll(self):
            return None

    ctx = run_context.begin('example-podcast', 'popen')
    monkeypatch.setattr(
        subprocess_registry.subprocess, 'Popen',
        lambda *args, **kwargs: FakeProcess(),
    )
    try:
        with subprocess_registry.tracked_popen(['ffmpeg', '-i', 'input.mp3']):
            pass
        assert ctx.timing.snapshot() == {'ffmpeg': 0.0}
    finally:
        run_context.end(ctx)


def test_ffmpeg_time_isolated_between_concurrent_runs(monkeypatch):
    class FakeProcess:
        returncode = 0

        def communicate(self, **kwargs):
            return b'', b''

    clocks = {}
    clock_lock = threading.Lock()

    def monotonic():
        ident = threading.get_ident()
        with clock_lock:
            values = clocks.setdefault(ident, iter([30.0, 31.0]))
        return next(values)

    monkeypatch.setattr(
        subprocess_registry, 'time',
        SimpleNamespace(monotonic=monotonic),
    )
    monkeypatch.setattr(
        subprocess_registry.subprocess, 'Popen',
        lambda *args, **kwargs: FakeProcess(),
    )
    contexts = []
    contexts_lock = threading.Lock()
    workers_ready = threading.Barrier(2)

    def run_bound(episode_id):
        ctx = run_context.begin('example-podcast', episode_id)
        with contexts_lock:
            contexts.append(ctx)
        try:
            workers_ready.wait(timeout=2)
            subprocess_registry.tracked_run(['ffmpeg', '-i', 'input.mp3'])
        finally:
            run_context.end(ctx)

    threads = [threading.Thread(target=run_bound, args=(episode_id,))
               for episode_id in ('one', 'two')]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)
        assert not thread.is_alive()
    assert sorted(ctx.timing.snapshot()['ffmpeg'] for ctx in contexts) == [1.0, 1.0]


def test_worker_thread_propagates_ffmpeg_time_to_submitting_run(monkeypatch):
    class FakeProcess:
        returncode = 0

        def communicate(self, **kwargs):
            return b'', b''

    ctx = run_context.begin('example-podcast', 'worker')
    clock = iter([40.0, 42.0])
    monkeypatch.setattr(
        subprocess_registry, 'time',
        SimpleNamespace(monotonic=lambda: next(clock)),
    )
    monkeypatch.setattr(
        subprocess_registry.subprocess, 'Popen',
        lambda *args, **kwargs: FakeProcess(),
    )
    worker = threading.Thread(target=run_context.run_in_worker_thread(
        lambda: subprocess_registry.tracked_run(['ffmpeg', '-i', 'input.mp3'])))
    try:
        worker.start()
        worker.join(timeout=2)
        assert not worker.is_alive()
        assert ctx.timing.snapshot()['ffmpeg'] == 2.0
    finally:
        run_context.end(ctx)


def test_failed_history_persists_partial_timings(monkeypatch):
    captured = {}
    fake_db = SimpleNamespace(
        get_podcast_by_slug=lambda slug: {'id': 7, 'title': 'Example'},
        get_run_usage_totals=lambda run_id: {
            'input_tokens': 0, 'output_tokens': 0, 'cost_usd': 0,
        },
        record_processing_history=lambda **kwargs: captured.update(kwargs) or 11,
        set_history_log_pointer=lambda *args: None,
    )
    monkeypatch.setattr(processing.run_log, 'current_recorder', lambda: None)
    ctx = run_context.begin('example-podcast', 'failed', run_id='run-failed')
    ctx.timing.add('transcription', 7.5)
    try:
        processing._record_history_row(
            fake_db, 'example-podcast', 'failed', 'Episode', 'Example',
            status='failed', processing_time=8.0, ads_detected=0,
            token_totals={'input_tokens': 0, 'output_tokens': 0, 'cost': 0},
            error_message='failed', run_stats={'mode': 'auto'},
        )
    finally:
        run_context.end(ctx)

    assert captured['status'] == 'failed'
    assert captured['processing_stats']['timings'] == {
        'ffmpeg': 0.0, 'transcription': 7.5,
    }


def test_finalize_timing_is_complete_before_history_snapshot():
    ctx = run_context.begin('example-podcast', 'finalize', run_id='run-finalize')
    clock = iter([20.0, 24.0])
    ctx.timing = run_context.RunTiming(clock=lambda: next(clock))
    ctx.timing.add('ffmpeg', 0.0)
    snapshots = []
    try:
        with ExitStack() as stack:
            p = lambda *a, **k: stack.enter_context(patch.object(*a, **k))
            p(processing, '_persist_episode_state')
            p(processing, '_refresh_rss_for_slug')
            p(processing, '_log_completion_summary', return_value={
                'input_tokens': 0, 'output_tokens': 0, 'cost': 0,
            })
            p(processing, '_record_history_and_event',
              side_effect=lambda *args, **kwargs: snapshots.append(ctx.timing.snapshot()))

            processing._finalize_episode(
                'example-podcast', 'finalize', 'Episode', 'Example',
                0, 0, 0, 60.0, 60.0, 0.0, run_stats={'mode': 'auto'},
            )
    finally:
        run_context.end(ctx)

    assert snapshots == [{'ffmpeg': 0.0, 'finalize': 4.0}]


def test_recut_records_cut_and_asset_stages():
    marker = {
        'start': 10.0, 'end': 20.0, 'category': 'sponsor',
        'action_applied': 'remove', 'was_cut': True, 'confidence': 0.95,
    }
    ctx = run_context.begin('example-podcast', 'recut')
    clock = iter([1.0, 3.0, 4.0, 7.0])
    ctx.timing = run_context.RunTiming(clock=lambda: next(clock))
    ctx.timing.add('ffmpeg', 0.0)
    try:
        with ExitStack() as stack:
            p = lambda *a, **k: stack.enter_context(patch.object(*a, **k))
            db = p(processing, 'db')
            storage = p(processing, 'storage')
            p(processing, 'status_service')
            p(processing, '_copy_retained_original_to_temp', return_value='/tmp/work.mp3')
            p(processing, '_build_recut_ad_list', return_value=([marker], [marker]))
            p(processing, '_partition_keep_ads', return_value=([], [marker]))
            p(processing, '_generate_assets')
            p(processing, '_finalize_episode')
            audio_processor = p(processing, 'AudioProcessor').return_value
            p(processing.os.path, 'exists', return_value=False)
            p(processing.shutil, 'move')

            db.get_episode.return_value = {'podcast_id': 1, 'processed_version': 0}
            db.get_original_segments.return_value = [{'start': 0.0, 'end': 60.0}]
            db.get_all_settings.return_value = {}
            db.resolve_segment_actions.return_value = {'sponsor': 'remove'}
            storage.get_original_path.return_value.exists.return_value = True
            storage.get_applied_cuts.return_value = None
            storage.get_episode_path.return_value = '/tmp/final.mp3'
            audio_processor.get_audio_duration.return_value = 60.0
            audio_processor.process_episode.return_value = (
                '/tmp/cut.mp3', [{'start': 10.0, 'end': 20.0}],
            )

            assert processing._recut_episode(
                'example-podcast', 'recut', 'Episode', 'Example', 'Description',
                0.0, podcast_row={'id': 1},
            ) is True
    finally:
        run_context.end(ctx)

    assert ctx.timing.snapshot() == {
        'ffmpeg': 0.0, 'cut': 2.0, 'assets': 3.0,
    }
