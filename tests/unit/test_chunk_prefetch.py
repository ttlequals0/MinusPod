"""Chunk extraction prefetcher: extraction overlaps GPU inference.

The chunked GPU loop takes each chunk from a _ChunkPrefetcher instead of
extracting inline, so the ffmpeg pass for chunk N+1 runs while chunk N
transcribes. These tests pin the pieces that keep that safe: boundary math
that mirrors the loop, stale-queue invalidation when the chunk size changes
mid-run, temp-file cleanup for extractions nobody consumed, and unchanged
error semantics at take().
"""

import os
import tempfile
import threading
import time
from unittest.mock import patch

import pytest

from transcriber import (
    _chunk_bounds_ahead,
    _ChunkPrefetcher,
    EXTRACT_PREFETCH_AHEAD,
)
from utils.errors import AudioExtractionTimeout


def _tmp_wav():
    tmp = tempfile.NamedTemporaryFile(suffix='.wav', delete=False)
    tmp.close()
    return tmp.name


class TestChunkBoundsAhead:
    def test_mirrors_the_loop_math_with_overlap(self):
        # 3600s of audio, 1800s chunks, 30s overlap: two chunks, only the
        # first carries the overlap because the second ends the file.
        assert _chunk_bounds_ahead(0, 1800, 3600, 30, 5) == [
            (0, 1830), (1800, 3600),
        ]

    def test_caps_at_the_requested_count(self):
        bounds = _chunk_bounds_ahead(0, 600, 3600, 30, 2)
        assert bounds == [(0, 630), (600, 1230)]

    def test_returns_nothing_at_end_of_audio(self):
        assert _chunk_bounds_ahead(3600, 1800, 3600, 30, 2) == []

    def test_tail_chunk_never_reaches_past_the_file(self):
        bounds = _chunk_bounds_ahead(3000, 1800, 3600, 30, 2)
        assert bounds == [(3000, 3600)]


class TestChunkPrefetcher:
    def test_take_returns_the_extraction_result(self):
        with patch('transcriber.extract_audio_chunk', return_value='/tmp/x.wav') as ex:
            p = _ChunkPrefetcher('/tmp/in.mp3')
            try:
                p.ensure((0, 1830))
                assert p.take((0, 1830)) == '/tmp/x.wav'
            finally:
                p.close()
        ex.assert_called_once_with('/tmp/in.mp3', 0, 1830, preprocess=True)

    def test_schedule_ahead_queues_the_lookahead(self):
        calls = []

        def extract(path, start, end, **kwargs):
            calls.append((start, end))
            return _tmp_wav()

        with patch('transcriber.extract_audio_chunk', side_effect=extract):
            p = _ChunkPrefetcher('/tmp/in.mp3')
            try:
                p.ensure((0, 1830))
                p.schedule_ahead(1800, 1800, 7200, 30)
                path = p.take((0, 1830))
                _wait_for(lambda: len(calls) == 1 + EXTRACT_PREFETCH_AHEAD)
            finally:
                p.close()
        os.unlink(path)
        assert (1800, 3630) in calls
        assert (3600, 5430) in calls

    def test_stale_queue_is_discarded_and_its_files_removed(self):
        made = []

        def extract(path, start, end, **kwargs):
            made.append(_tmp_wav())
            return made[-1]

        with patch('transcriber.extract_audio_chunk', side_effect=extract):
            p = _ChunkPrefetcher('/tmp/in.mp3')
            try:
                p.ensure((0, 1830))
                consumed = p.take((0, 1830))
                p.schedule_ahead(1800, 1800, 7200, 30)
                # Chunk size changed (OOM shrink): the next ask misses.
                p.ensure((0, 930))
                fresh = p.take((0, 930))
            finally:
                p.close()
        # Consumed and fresh files belong to the caller; the two stale
        # lookahead extractions were unlinked by the discard callback.
        stale = [f for f in made if f not in (consumed, fresh)]
        assert len(stale) == 2
        _wait_for(lambda: not any(os.path.exists(f) for f in stale))
        os.unlink(consumed)
        os.unlink(fresh)

    def test_take_surfaces_extraction_timeout(self):
        with patch('transcriber.extract_audio_chunk',
                   side_effect=AudioExtractionTimeout('ffmpeg exceeded budget')):
            p = _ChunkPrefetcher('/tmp/in.mp3')
            try:
                p.ensure((0, 1830))
                with pytest.raises(AudioExtractionTimeout):
                    p.take((0, 1830))
            finally:
                p.close()

    def test_close_removes_unconsumed_files(self):
        made = []

        def extract(path, start, end, **kwargs):
            made.append(_tmp_wav())
            return made[-1]

        with patch('transcriber.extract_audio_chunk', side_effect=extract):
            p = _ChunkPrefetcher('/tmp/in.mp3')
            p.ensure((0, 1830))
            _wait_for(lambda: len(made) == 1)
            p.close()
        _wait_for(lambda: not os.path.exists(made[0]))

    def test_workers_register_with_the_run_log(self):
        idents = []

        def extract(path, start, end, **kwargs):
            idents.append(threading.get_ident())
            return None

        with patch('transcriber.extract_audio_chunk', side_effect=extract), \
             patch('run_log.register_worker_thread') as reg, \
             patch('run_log.unregister_worker_thread') as unreg:
            p = _ChunkPrefetcher('/tmp/in.mp3')
            try:
                p.ensure((0, 1830))
                p.take((0, 1830))
            finally:
                p.close()
        assert reg.called
        assert unreg.called
        assert idents and idents[0] != threading.get_ident()


def _wait_for(condition, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(0.02)
    assert condition()
