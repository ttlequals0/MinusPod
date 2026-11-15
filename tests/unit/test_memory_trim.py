from contextlib import nullcontext
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tests.app_bootstrap import bootstrap

bootstrap('memory_trim_')

from main_app import processing
from main_app.pipeline import finalize
from utils import memory

ROOT = Path(__file__).resolve().parents[2]


def test_trim_calls_malloc_trim_and_logs_rss(caplog):
    trim = MagicMock()
    with patch.object(memory, '_malloc_trim', trim), \
            patch.object(memory, '_rss_mib', side_effect=[100.0, 60.0]), \
            caplog.at_level('INFO', logger='utils.memory'):
        assert memory.trim_idle_memory() is True
    trim.assert_called_once_with(0)
    assert 'RSS 100 MiB -> 60 MiB' in caplog.text


def test_trim_without_rss_still_trims():
    trim = MagicMock()
    with patch.object(memory, '_malloc_trim', trim), \
            patch.object(memory, '_rss_mib', return_value=None):
        assert memory.trim_idle_memory() is True
    trim.assert_called_once_with(0)


def test_trim_noop_when_libc_lacks_symbol():
    with patch.object(memory, '_malloc_trim', None):
        assert memory.trim_idle_memory() is False


def test_load_returns_none_when_library_or_symbol_missing():
    with patch.object(memory.ctypes, 'CDLL', side_effect=OSError):
        assert memory._load_malloc_trim() is None
    with patch.object(memory.ctypes, 'CDLL', return_value=object()):
        assert memory._load_malloc_trim() is None


def test_trim_never_raises():
    with patch.object(memory, '_malloc_trim', MagicMock(side_effect=OSError('boom'))):
        assert memory.trim_idle_memory() is False


def _finish_run(active, pending):
    queue = MagicMock()
    queue.get_current.return_value = active
    with (
        patch.object(processing, 'ProcessingQueue', return_value=queue),
        patch.object(processing, 'process_episode'),
        patch.object(processing, 'pattern_catalog_scope', return_value=nullcontext()),
        patch.object(finalize, '_start_run_log', return_value=None),
        patch.object(finalize, '_end_run_log'),
        patch.object(processing.db, 'get_episode', return_value=None),
        patch.object(processing.db, 'clear_leaked_transaction'),
        patch.object(processing.db, 'count_pending_queued_episodes', return_value=pending),
        patch.object(processing, 'trim_idle_memory') as trim,
    ):
        processing._process_episode_background(
            'feed', 'aa11bb22cc33', 'https://example.com/e.mp3',
            'Episode', 'Feed', None, None, run_id='run-1')
    return trim


def test_drain_trims_once():
    assert _finish_run([], 0).call_count == 1


def test_no_trim_while_another_run_is_active():
    assert _finish_run([('feed', 'other')], 0).call_count == 0


def test_no_trim_while_episodes_are_queued():
    assert _finish_run([], 3).call_count == 0


def test_trim_check_error_does_not_break_run():
    queue = MagicMock()
    queue.get_current.side_effect = RuntimeError('db down')
    with patch.object(processing, 'trim_idle_memory') as trim:
        assert processing._trim_when_drained(queue) is None
    trim.assert_not_called()


@pytest.mark.parametrize('name', ['Dockerfile', 'Dockerfile.cpu'])
def test_dockerfiles_set_allocator_defaults(name):
    text = (ROOT / name).read_text()
    assert 'MALLOC_ARENA_MAX=2' in text
    assert 'OPENBLAS_NUM_THREADS=1' in text
