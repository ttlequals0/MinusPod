import os
import subprocess
import time
from unittest.mock import patch

import numpy as np

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('peaks_retain_test_')
import audio_peaks as ap  # noqa: E402
import main_app.processing as processing  # noqa: E402


def _gen_wav(path, seconds=1.0, freq=440):
    subprocess.run(['ffmpeg', '-y', '-f', 'lavfi', '-i',
                    f'sine=frequency={freq}:duration={seconds}',
                    '-ac', '1', '-ar', '44100', str(path)], check=True,
                   capture_output=True)


def test_writes_file_without_decoding(tmp_path):
    src = tmp_path / 'e-original.wav'
    _gen_wav(src)
    with patch('audio_peaks.tracked_popen') as popen, patch('audio_peaks.tracked_run') as run:
        assert ap.maybe_write_peaks_file(src, np.array([3, 4], dtype='<u2')) is True
        popen.assert_not_called()
        run.assert_not_called()
    assert ap.read_peaks_file(ap.peaks_file_path(src)).bucket_count == 2


def test_none_writes_nothing(tmp_path):
    src = tmp_path / 'e-original.wav'
    _gen_wav(src)
    assert ap.maybe_write_peaks_file(src, None) is False
    assert not ap.peaks_file_path(src).exists()


def test_restamps_existing_valid_file(tmp_path):
    src = tmp_path / 'e-original.wav'
    _gen_wav(src)
    ap.maybe_write_peaks_file(src, np.array([9], dtype='<u2'))
    future = time.time_ns() + 10_000_000_000
    os.utime(src, ns=(future, future))
    with patch('audio_peaks.write_peaks_file') as w:
        assert ap.maybe_write_peaks_file(src, np.array([9], dtype='<u2')) is True
        w.assert_not_called()
    assert ap.read_peaks_file(ap.peaks_file_path(src)).source_mtime_ns == src.stat().st_mtime_ns


def test_changed_content_is_rewritten(tmp_path):
    src = tmp_path / 'e-original.wav'
    _gen_wav(src)
    ap.maybe_write_peaks_file(src, np.array([9], dtype='<u2'))
    _gen_wav(src, seconds=2.0, freq=880)
    assert ap.maybe_write_peaks_file(src, np.array([5, 6, 7], dtype='<u2')) is True
    assert list(ap.read_peaks_file(ap.peaks_file_path(src)).buckets) == [5, 6, 7]


def test_discard_stale_removes_mismatched_file(tmp_path):
    src = tmp_path / 'e-original.wav'
    _gen_wav(src)
    ap.maybe_write_peaks_file(src, np.array([9], dtype='<u2'))
    _gen_wav(src, seconds=2.0, freq=880)
    assert ap.discard_stale_peaks_file(src) is True
    assert not ap.peaks_file_path(src).exists()


def test_discard_stale_keeps_valid_file_and_ignores_missing(tmp_path):
    src = tmp_path / 'e-original.wav'
    _gen_wav(src)
    assert ap.discard_stale_peaks_file(src) is False
    ap.maybe_write_peaks_file(src, np.array([9], dtype='<u2'))
    assert ap.discard_stale_peaks_file(src) is False
    assert ap.peaks_file_path(src).exists()


def test_store_peaks_file_writes_from_analysis_result(tmp_path):
    src = tmp_path / 'e-original.wav'
    _gen_wav(src)
    result = type('R', (), {'peaks_10ms': np.array([1, 2, 3], dtype='<u2')})()
    processing._store_peaks_file('s', 'e', src, result)
    assert ap.read_peaks_file(ap.peaks_file_path(src)).bucket_count == 3


def test_store_peaks_file_without_analysis_writes_nothing(tmp_path):
    src = tmp_path / 'e-original.wav'
    _gen_wav(src)
    processing._store_peaks_file('s', 'e', src, None)
    assert not ap.peaks_file_path(src).exists()


def test_store_peaks_file_drops_stale_file_when_no_peaks(tmp_path):
    src = tmp_path / 'e-original.wav'
    _gen_wav(src)
    ap.maybe_write_peaks_file(src, np.array([9], dtype='<u2'))
    _gen_wav(src, seconds=2.0, freq=880)
    processing._store_peaks_file('s', 'e', src, None)
    assert not ap.peaks_file_path(src).exists()


def test_store_peaks_file_swallows_errors(tmp_path):
    src = tmp_path / 'e-original.wav'
    _gen_wav(src)
    result = type('R', (), {'peaks_10ms': np.array([1], dtype='<u2')})()
    with patch('audio_peaks.write_peaks_file', side_effect=OSError('disk full')):
        processing._store_peaks_file('s', 'e', src, result)
