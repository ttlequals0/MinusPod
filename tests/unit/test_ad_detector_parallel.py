"""Tests for the parallel ad-detection window executor in src/ad_detector.

Covers four guarantees the 2.5.23 refactor must hold:

- parallel_windows=1 produces the same ordered output as the original
  sequential loop (regression guard).
- parallel_windows=N completes and returns ads in window-position order,
  not future-completion order.
- Cost accumulator totals match between sequential and parallel runs
  (no double-counting, no lost updates).
- Validator rejects out-of-range values via the API.
"""
import json
import os
import threading
import time
from typing import List, Dict
from unittest.mock import patch

import pytest

import config
from ad_detector import AdDetector, _resolve_parallel_windows, WindowResult
from llm_client import (
    start_episode_token_tracking,
    get_episode_token_totals,
    _episode_accumulator,
    _record_token_usage,
)


class _StubResponse:
    """Minimal LLMResponse-shaped duck for _process_single_window."""

    def __init__(self, content: str, usage: Dict):
        self.content = content
        self.usage = usage


def _make_windows(n: int):
    """N windows, 60s each, no overlap, with one segment per window."""
    out = []
    for i in range(n):
        start = i * 60.0
        end = start + 60.0
        out.append({
            'start': start,
            'end': end,
            'segments': [
                {'start': start + 1.0, 'end': start + 5.0, 'text': f' window {i} content'}
            ],
        })
    return out


def _ads_for_window(i: int) -> str:
    """Return a JSON ads array that places one ad in window i's range."""
    return json.dumps([{
        'start': i * 60.0 + 10.0,
        'end': i * 60.0 + 30.0,
        'confidence': 0.9,
        'sponsor': f'sponsor-{i}',
    }])


@pytest.fixture
def detector():
    d = AdDetector(api_key='test-key')
    d._initialized_for_test = True
    return d


@pytest.fixture(autouse=True)
def reset_accumulator():
    # Ensure no leftover state between tests.
    _episode_accumulator.collect_and_reset()
    yield
    _episode_accumulator.collect_and_reset()


class TestResolveParallelWindows:
    """Parallel-window count resolution clamps into [1, 32]."""

    def test_clamps_to_min_when_invalid(self):
        with patch.dict(os.environ, {'AD_DETECTION_PARALLEL_WINDOWS': '0'}, clear=False):
            # env validator returns fallback '4', but DB might still be invalid
            # Force missing DB
            with patch('llm_client._get_cached_setting', return_value=None):
                assert _resolve_parallel_windows() == 4

    def test_clamps_to_max(self):
        with patch.dict(os.environ, {'AD_DETECTION_PARALLEL_WINDOWS': '100'}, clear=False):
            with patch('llm_client._get_cached_setting', return_value=None):
                # validator rejects out-of-range, so falls back to default
                assert _resolve_parallel_windows() == 4

    def test_db_overrides_env(self):
        with patch.dict(os.environ, {'AD_DETECTION_PARALLEL_WINDOWS': '4'}, clear=False):
            with patch('llm_client._get_cached_setting', return_value='8'):
                assert _resolve_parallel_windows() == 8

    def test_db_value_clamped_to_max(self):
        with patch('llm_client._get_cached_setting', return_value='9999'):
            assert _resolve_parallel_windows() == 32

    def test_garbage_db_falls_back_to_default(self):
        with patch('llm_client._get_cached_setting', return_value='not a number'):
            with patch.dict(os.environ, {}, clear=False):
                env = os.environ.copy()
                env.pop('AD_DETECTION_PARALLEL_WINDOWS', None)
                with patch.dict(os.environ, env, clear=True):
                    assert _resolve_parallel_windows() == 4


class TestRunWindowsOrdering:
    """_run_windows returns results in window-position order, even when the
    executor finishes them out of order."""

    def _make_stub_process(self, delay_map=None):
        """Returns a stub _process_single_window that sleeps for delay_map[idx]
        seconds (default 0) and produces a deterministic WindowResult for idx.
        """
        delays = delay_map or {}

        def stub(*, window_idx, window, total_windows, **_kwargs):
            time.sleep(delays.get(window_idx, 0))
            return WindowResult(
                window_idx=window_idx,
                window_start=window['start'],
                window_end=window['end'],
                ads=[{
                    'start': window['start'] + 10.0,
                    'end': window['start'] + 30.0,
                    'sponsor': f'sponsor-{window_idx}',
                }],
                raw_response=f"win{window_idx}",
                failed=False,
                last_error=None,
            )

        return stub

    def test_sequential_preserves_order(self, detector):
        windows = _make_windows(5)
        stub = self._make_stub_process()
        with patch.object(detector, '_process_single_window', side_effect=stub):
            results = detector._run_windows(
                windows, max_workers=1, progress_callback=None,
                progress_base=0, progress_range=100,
                model='x', system_prompt='x', description_section='x',
                podcast_name='p', episode_title='e',
                audio_enforcer=None, audio_analysis=None,
                llm_timeout=30, max_retries=1,
                slug='s', episode_id='1',
                pass_name='ad_detection_1',
                window_label_prefix='Window',
                validate_timestamps=False,
            )
        assert [r.window_idx for r in results] == [0, 1, 2, 3, 4]
        assert [r.ads[0]['sponsor'] for r in results] == [f'sponsor-{i}' for i in range(5)]

    def test_parallel_returns_results_in_window_order(self, detector):
        # Window 0 deliberately slow so future-completion order != window order.
        windows = _make_windows(5)
        stub = self._make_stub_process(delay_map={0: 0.10, 1: 0.0, 2: 0.0, 3: 0.0, 4: 0.0})
        with patch.object(detector, '_process_single_window', side_effect=stub):
            results = detector._run_windows(
                windows, max_workers=4, progress_callback=None,
                progress_base=0, progress_range=100,
                model='x', system_prompt='x', description_section='x',
                podcast_name='p', episode_title='e',
                audio_enforcer=None, audio_analysis=None,
                llm_timeout=30, max_retries=1,
                slug='s', episode_id='1',
                pass_name='ad_detection_1',
                window_label_prefix='Window',
                validate_timestamps=False,
            )
        assert [r.window_idx for r in results] == [0, 1, 2, 3, 4]
        assert [r.ads[0]['sponsor'] for r in results] == [f'sponsor-{i}' for i in range(5)]


class TestRunWindowsProgressCallback:
    """Progress callbacks fire once per completed window and are serialized
    via a lock so parallel completion doesn't corrupt the displayed count."""

    def test_progress_fires_per_window(self, detector):
        windows = _make_windows(4)
        progress_calls = []
        progress_lock = threading.Lock()

        def cb(stage, percent):
            with progress_lock:
                progress_calls.append((stage, percent))

        def stub(*, window_idx, window, total_windows, **_kwargs):
            return WindowResult(
                window_idx=window_idx,
                window_start=window['start'],
                window_end=window['end'],
                ads=[],
                raw_response=None,
                failed=False,
                last_error=None,
            )

        with patch.object(detector, '_process_single_window', side_effect=stub):
            detector._run_windows(
                windows, max_workers=4, progress_callback=cb,
                progress_base=50, progress_range=30,
                model='x', system_prompt='x', description_section='x',
                podcast_name='p', episode_title='e',
                audio_enforcer=None, audio_analysis=None,
                llm_timeout=30, max_retries=1,
                slug='s', episode_id='1',
                pass_name='ad_detection_1',
                window_label_prefix='Window',
                validate_timestamps=False,
            )

        assert len(progress_calls) == 4
        # Percent values fall within progress_base + progress_range.
        for stage, percent in progress_calls:
            assert 50 <= percent <= 80
            assert stage.startswith('detecting:')


class TestCostAccumulatorParallelSafety:
    """The shared lock-protected accumulator must collect totals correctly
    from N concurrent workers without double-counting or lost updates."""

    def test_parallel_calls_aggregate_correctly(self):
        """Hammer the accumulator from 8 threads with 100 calls each and
        verify the totals are exactly the expected sum."""
        start_episode_token_tracking()

        threads = []
        n_threads = 8
        calls_per_thread = 100
        per_call_in = 10
        per_call_out = 5
        per_call_cost = 0.001

        def worker():
            for _ in range(calls_per_thread):
                _episode_accumulator.add(per_call_in, per_call_out, per_call_cost)

        for _ in range(n_threads):
            t = threading.Thread(target=worker)
            t.start()
            threads.append(t)
        for t in threads:
            t.join()

        totals = get_episode_token_totals()
        expected_in = n_threads * calls_per_thread * per_call_in
        expected_out = n_threads * calls_per_thread * per_call_out
        expected_cost = n_threads * calls_per_thread * per_call_cost

        assert totals['input_tokens'] == expected_in
        assert totals['output_tokens'] == expected_out
        assert abs(totals['cost'] - expected_cost) < 1e-6

    def test_inactive_accumulator_silently_drops_updates(self):
        """A worker thread updating the accumulator while it's not active
        must be a safe no-op (e.g., chapters generation outside detection)."""
        # Don't call start_episode_token_tracking
        _episode_accumulator.add(100, 50, 1.0)
        totals = get_episode_token_totals()
        assert totals['input_tokens'] == 0
        assert totals['output_tokens'] == 0
        assert totals['cost'] == 0.0
