import logging
import os
import subprocess
from types import SimpleNamespace
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
    with patch('audio_peaks.tracked_popen') as popen:
        assert ap.maybe_write_peaks_file(src, np.array([3, 4], dtype='<u2')) is True
        popen.assert_not_called()
    assert ap.read_peaks_window(ap.peaks_file_path(src)).bucket_count == 2


def test_none_writes_nothing(tmp_path):
    src = tmp_path / 'e-original.wav'
    _gen_wav(src)
    assert ap.maybe_write_peaks_file(src, None) is False
    assert not ap.peaks_file_path(src).exists()


def test_a_matching_stamp_writes_nothing(tmp_path):
    src = tmp_path / 'e-original.wav'
    _gen_wav(src)
    ap.maybe_write_peaks_file(src, np.array([9], dtype='<u2'))
    with patch('audio_peaks.write_peaks_file') as w:
        assert ap.maybe_write_peaks_file(src, np.array([9], dtype='<u2')) is True
        w.assert_not_called()


def test_the_retention_write_never_hashes_the_original(tmp_path):
    src = tmp_path / 'e-original.wav'
    _gen_wav(src)
    with patch('audio_peaks._hash_open_source', side_effect=AssertionError('hashed')):
        assert ap.maybe_write_peaks_file(src, np.array([9], dtype='<u2')) is True


def test_a_changed_mtime_is_rewritten_from_the_new_maxima(tmp_path):
    src = tmp_path / 'e-original.wav'
    _gen_wav(src)
    ap.maybe_write_peaks_file(src, np.array([9], dtype='<u2'))
    future = src.stat().st_mtime_ns + 10_000_000_000
    os.utime(src, ns=(future, future))
    assert ap.maybe_write_peaks_file(src, np.array([4, 5], dtype='<u2')) is True
    data = ap.read_peaks_window(ap.peaks_file_path(src))
    assert list(data.buckets) == [4, 5] and data.source_mtime_ns == future


def test_changed_content_is_rewritten(tmp_path):
    src = tmp_path / 'e-original.wav'
    _gen_wav(src)
    ap.maybe_write_peaks_file(src, np.array([9], dtype='<u2'))
    _gen_wav(src, seconds=2.0, freq=880)
    assert ap.maybe_write_peaks_file(src, np.array([5, 6, 7], dtype='<u2')) is True
    assert list(ap.read_peaks_window(ap.peaks_file_path(src)).buckets) == [5, 6, 7]


def test_same_size_different_bytes_is_rewritten(tmp_path):
    src = tmp_path / 'e-original.wav'
    _gen_wav(src)
    ap.maybe_write_peaks_file(src, np.array([9, 9], dtype='<u2'))
    size = src.stat().st_size
    _gen_wav(src, freq=880)
    assert src.stat().st_size == size
    assert ap.maybe_write_peaks_file(src, np.array([5, 6], dtype='<u2')) is True
    assert list(ap.read_peaks_window(ap.peaks_file_path(src)).buckets) == [5, 6]


def test_store_peaks_file_writes_from_analysis_result(tmp_path):
    src = tmp_path / 'e-original.wav'
    _gen_wav(src)
    result = type('R', (), {'peaks_10ms': np.array([1, 2, 3], dtype='<u2')})()
    processing._store_peaks_file('s', 'e', src, result)
    assert ap.read_peaks_window(ap.peaks_file_path(src)).bucket_count == 3


def test_store_peaks_file_without_analysis_writes_nothing(tmp_path):
    src = tmp_path / 'e-original.wav'
    _gen_wav(src)
    processing._store_peaks_file('s', 'e', src, None)
    assert not ap.peaks_file_path(src).exists()


def test_store_peaks_file_without_peaks_leaves_the_stamp_to_catch_a_stale_file(tmp_path):
    src = tmp_path / 'e-original.wav'
    _gen_wav(src)
    ap.maybe_write_peaks_file(src, np.array([9], dtype='<u2'))
    _gen_wav(src, seconds=2.0, freq=880)
    processing._store_peaks_file('s', 'e', src, None)
    assert not ap.validate_or_restamp_peaks_file(ap.peaks_file_path(src), src)


def test_store_peaks_file_swallows_errors(tmp_path):
    src = tmp_path / 'e-original.wav'
    _gen_wav(src)
    result = type('R', (), {'peaks_10ms': np.array([1], dtype='<u2')})()
    with patch('audio_peaks.write_peaks_file', side_effect=OSError('disk full')) as write:
        assert processing._store_peaks_file('s', 'e', src, result) is None
    write.assert_called_once()
    assert not ap.peaks_file_path(src).exists()


class _Storage:
    def __init__(self, root):
        self.root = root

    def get_original_path(self, slug, episode_id):
        return self.root / f'{episode_id}-original.wav'


def _retain(processing, tmp_path, src, peaks, keep=True, duration=None):
    result = SimpleNamespace(peaks_10ms=peaks)
    with patch.object(processing, 'storage', _Storage(tmp_path / 'ep')):
        return processing._retain_original('s', 'e', src, keep, result, duration)


def _work_copy(tmp_path, name, freq=440):
    path = tmp_path / name
    _gen_wav(path, freq=freq)
    return path


def test_retain_moves_original_and_writes_peaks_for_final_file(tmp_path):
    final = tmp_path / 'ep' / 'e-original.wav'
    src = _work_copy(tmp_path, 'work.wav')
    assert _retain(processing, tmp_path, src, np.array([1, 2, 3], dtype='<u2')) is True
    assert not src.exists()
    data = ap.read_peaks_window(ap.peaks_file_path(final))
    assert list(data.buckets) == [1, 2, 3]
    assert data.source_size == final.stat().st_size
    assert data.source_mtime_ns == final.stat().st_mtime_ns
    assert data.source_sha256 == bytes(32)


def test_retain_with_keep_original_off_writes_nothing(tmp_path):
    src = _work_copy(tmp_path, 'work.wav')
    assert _retain(processing, tmp_path, src, np.array([1], dtype='<u2'), keep=False) is False
    assert src.exists()
    assert not (tmp_path / 'ep').exists()


def test_reprocess_same_bytes_with_a_new_mtime_rewrites_the_stamp(tmp_path):
    final = tmp_path / 'ep' / 'e-original.wav'
    first = _work_copy(tmp_path, 'work1.wav')
    _retain(processing, tmp_path, first, np.array([1, 2], dtype='<u2'))
    peaks_path = ap.peaks_file_path(final)
    second = tmp_path / 'work2.wav'
    second.write_bytes(final.read_bytes())
    later = final.stat().st_mtime_ns + 10_000_000_000
    os.utime(second, ns=(later, later))
    _retain(processing, tmp_path, second, np.array([7, 7], dtype='<u2'))
    data = ap.read_peaks_window(peaks_path)
    assert list(data.buckets) == [7, 7]
    assert data.source_mtime_ns == final.stat().st_mtime_ns == later


def test_reprocess_same_size_different_bytes_rewrites(tmp_path):
    final = tmp_path / 'ep' / 'e-original.wav'
    first = _work_copy(tmp_path, 'work1.wav')
    _retain(processing, tmp_path, first, np.array([1, 2], dtype='<u2'))
    size = final.stat().st_size
    second = _work_copy(tmp_path, 'work2.wav', freq=880)
    assert second.stat().st_size == size
    _retain(processing, tmp_path, second, np.array([8, 9], dtype='<u2'))
    assert list(ap.read_peaks_window(ap.peaks_file_path(final)).buckets) == [8, 9]


def test_reprocess_without_peaks_and_new_bytes_leaves_a_file_the_stamp_rejects(tmp_path):
    final = tmp_path / 'ep' / 'e-original.wav'
    first = _work_copy(tmp_path, 'work1.wav')
    _retain(processing, tmp_path, first, np.array([1, 2], dtype='<u2'))
    second = _work_copy(tmp_path, 'work2.wav', freq=880)
    _retain(processing, tmp_path, second, None)
    assert final.exists()
    assert not ap.validate_or_restamp_peaks_file(ap.peaks_file_path(final), final)


def test_failed_write_on_reprocess_logs_once_and_leaves_no_stale_or_temp_file(tmp_path, caplog):
    final = tmp_path / 'ep' / 'e-original.wav'
    first = _work_copy(tmp_path, 'work1.wav')
    _retain(processing, tmp_path, first, np.array([1, 2], dtype='<u2'))
    second = _work_copy(tmp_path, 'work2.wav', freq=880)
    with patch('audio_peaks._write_atomic', side_effect=OSError('disk full')), \
            caplog.at_level(logging.WARNING):
        assert _retain(processing, tmp_path, second, np.array([8, 9], dtype='<u2')) is True
    warnings = [r for r in caplog.records if r.name == 'podcast.audio' and r.levelno >= logging.WARNING]
    assert len(warnings) == 1
    assert final.exists()
    assert sorted(p.name for p in final.parent.iterdir()) == ['e-original.peaks', 'e-original.wav']
    assert not ap.validate_or_restamp_peaks_file(ap.peaks_file_path(final), final)


def test_retain_records_the_exact_duration_on_the_episode_row(tmp_path):
    src = _work_copy(tmp_path, 'work.wav')
    with patch.object(processing, 'db') as fake_db:
        assert _retain(processing, tmp_path, src, np.array([1], dtype='<u2'), duration=1234.567) is True
    fake_db.upsert_episode.assert_called_once_with('s', 'e', original_duration=1234.567)


def test_retain_without_a_duration_writes_no_row(tmp_path):
    src = _work_copy(tmp_path, 'work.wav')
    with patch.object(processing, 'db') as fake_db:
        _retain(processing, tmp_path, src, np.array([1], dtype='<u2'))
    fake_db.upsert_episode.assert_not_called()


def test_a_failed_duration_write_does_not_fail_the_retention(tmp_path):
    src = _work_copy(tmp_path, 'work.wav')
    with patch.object(processing, 'db') as fake_db:
        fake_db.upsert_episode.side_effect = RuntimeError('locked')
        assert _retain(processing, tmp_path, src, np.array([1], dtype='<u2'), duration=5.0) is True
    assert ap.peaks_file_path(tmp_path / 'ep' / 'e-original.wav').exists()
