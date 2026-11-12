import os
import struct
import subprocess
import time

import numpy as np

import audio_peaks as ap


def _gen_wav(path, seconds=2.0):
    subprocess.run(['ffmpeg', '-y', '-f', 'lavfi', '-i',
                    f'sine=frequency=440:duration={seconds}',
                    '-ac', '1', '-ar', '44100', str(path)], check=True,
                   capture_output=True)


def _src(tmp_path, seconds=2.0):
    src = tmp_path / 'a1b2c3-original.wav'
    _gen_wav(src, seconds)
    return src


def test_peaks_path_is_sibling():
    assert ap.peaks_file_path('/d/x-original.mp3').as_posix() == '/d/x-original.peaks'


def test_write_then_read_round_trip(tmp_path):
    src = _src(tmp_path)
    buckets = np.array([0, 100, 32768, 5], dtype='<u2')
    ap.write_peaks_file(ap.peaks_file_path(src), buckets, src)
    data = ap.read_peaks_file(ap.peaks_file_path(src))
    assert data.version == 1 and data.bucket_width_ms == 10
    assert data.bucket_count == 4
    assert data.source_size == src.stat().st_size
    assert data.source_mtime_ns == src.stat().st_mtime_ns
    assert len(data.source_sha256) == 32
    assert data.buckets.dtype == np.dtype('<u2')
    assert np.array_equal(data.buckets, buckets)


def test_header_layout_is_little_endian_64_bytes(tmp_path):
    src = _src(tmp_path)
    p = ap.peaks_file_path(src)
    ap.write_peaks_file(p, np.array([1, 2, 3], dtype='<u2'), src)
    raw = p.read_bytes()
    assert len(raw) == 64 + 6
    assert raw[:4] == ap.PEAKS_MAGIC
    assert struct.unpack_from('<HHI', raw, 4) == (1, 10, 3)
    assert raw[64:] == struct.pack('<3H', 1, 2, 3)


def test_write_accepts_other_dtypes_and_empty(tmp_path):
    src = _src(tmp_path)
    p = ap.peaks_file_path(src)
    ap.write_peaks_file(p, np.array([1, 2], dtype=np.int32), src)
    assert np.array_equal(ap.read_peaks_file(p).buckets, [1, 2])
    ap.write_peaks_file(p, np.array([], dtype='<u2'), src)
    assert ap.read_peaks_file(p).bucket_count == 0


def test_read_missing_file(tmp_path):
    assert ap.read_peaks_file(tmp_path / 'nope.peaks') is None


def test_read_rejects_bad_magic(tmp_path):
    p = tmp_path / 'x.peaks'
    p.write_bytes(b'XXXX' + b'\x00' * 60)
    assert ap.read_peaks_file(p) is None


def test_read_rejects_future_version(tmp_path):
    src = _src(tmp_path)
    p = ap.peaks_file_path(src)
    ap.write_peaks_file(p, np.array([1, 2], dtype='<u2'), src)
    raw = bytearray(p.read_bytes())
    struct.pack_into('<H', raw, 4, 2)
    p.write_bytes(raw)
    assert ap.read_peaks_file(p) is None


def test_read_rejects_other_bucket_width(tmp_path):
    src = _src(tmp_path)
    p = ap.peaks_file_path(src)
    ap.write_peaks_file(p, np.array([1, 2], dtype='<u2'), src)
    raw = bytearray(p.read_bytes())
    struct.pack_into('<H', raw, 6, 20)
    p.write_bytes(raw)
    assert ap.read_peaks_file(p) is None


def test_read_rejects_truncated_data(tmp_path):
    src = _src(tmp_path)
    p = ap.peaks_file_path(src)
    ap.write_peaks_file(p, np.arange(100, dtype='<u2'), src)
    p.write_bytes(p.read_bytes()[:64 + 10])
    assert ap.read_peaks_file(p) is None


def test_read_rejects_truncated_header(tmp_path):
    p = tmp_path / 'x.peaks'
    p.write_bytes(ap.PEAKS_MAGIC + b'\x01')
    assert ap.read_peaks_file(p) is None
    p.write_bytes(b'')
    assert ap.read_peaks_file(p) is None


def test_read_rejects_oversized_file(tmp_path):
    src = _src(tmp_path)
    p = ap.peaks_file_path(src)
    ap.write_peaks_file(p, np.arange(10, dtype='<u2'), src)
    p.write_bytes(p.read_bytes() + b'\x00\x00')
    assert ap.read_peaks_file(p) is None


def test_read_huge_declared_count_does_not_allocate(tmp_path, monkeypatch):
    src = _src(tmp_path)
    p = ap.peaks_file_path(src)
    ap.write_peaks_file(p, np.arange(4, dtype='<u2'), src)
    raw = bytearray(p.read_bytes())
    struct.pack_into('<I', raw, 8, 0xFFFFFFFF)
    p.write_bytes(raw)

    def boom(*a, **k):
        raise AssertionError('allocated before validating size')
    monkeypatch.setattr(ap.np, 'frombuffer', boom)
    monkeypatch.setattr(ap.np, 'fromfile', boom)
    assert ap.read_peaks_file(p) is None


def test_read_directory_returns_none(tmp_path):
    d = tmp_path / 'd.peaks'
    d.mkdir()
    assert ap.read_peaks_file(d) is None


def test_restamp_on_changed_mtime_same_hash(tmp_path):
    src = _src(tmp_path)
    p = ap.peaks_file_path(src)
    ap.write_peaks_file(p, np.array([7, 8], dtype='<u2'), src)
    future = time.time_ns() + 10_000_000_000
    os.utime(src, ns=(future, future))
    assert ap.validate_or_restamp_peaks_file(p, src) is True
    data = ap.read_peaks_file(p)
    assert data.source_mtime_ns == src.stat().st_mtime_ns
    assert np.array_equal(data.buckets, [7, 8])


def test_validate_true_without_rewrite_when_stamp_matches(tmp_path):
    src = _src(tmp_path)
    p = ap.peaks_file_path(src)
    ap.write_peaks_file(p, np.array([7, 8], dtype='<u2'), src)
    before = p.stat().st_mtime_ns
    assert ap.validate_or_restamp_peaks_file(p, src) is True
    assert p.stat().st_mtime_ns == before


def test_validate_false_on_changed_hash(tmp_path):
    src = _src(tmp_path)
    p = ap.peaks_file_path(src)
    ap.write_peaks_file(p, np.array([1], dtype='<u2'), src)
    _gen_wav(src, seconds=3.0)
    assert ap.validate_or_restamp_peaks_file(p, src) is False


def test_validate_false_on_same_size_different_content(tmp_path):
    src = tmp_path / 's-original.bin'
    src.write_bytes(b'a' * 1000)
    p = ap.peaks_file_path(src)
    ap.write_peaks_file(p, np.array([1], dtype='<u2'), src)
    src.write_bytes(b'b' * 1000)
    future = time.time_ns() + 10_000_000_000
    os.utime(src, ns=(future, future))
    assert ap.validate_or_restamp_peaks_file(p, src) is False


def test_validate_false_when_source_missing(tmp_path):
    src = _src(tmp_path)
    p = ap.peaks_file_path(src)
    ap.write_peaks_file(p, np.array([1], dtype='<u2'), src)
    src.unlink()
    assert ap.validate_or_restamp_peaks_file(p, src) is False


def test_validate_false_when_file_missing_or_corrupt(tmp_path):
    src = _src(tmp_path)
    p = ap.peaks_file_path(src)
    assert ap.validate_or_restamp_peaks_file(p, src) is False
    p.write_bytes(b'garbage')
    assert ap.validate_or_restamp_peaks_file(p, src) is False


def test_write_is_atomic_no_partial_on_failure(tmp_path, monkeypatch):
    src = _src(tmp_path)
    p = ap.peaks_file_path(src)
    monkeypatch.setattr(ap.os, 'replace',
                        lambda *a, **k: (_ for _ in ()).throw(OSError('boom')))
    try:
        ap.write_peaks_file(p, np.array([1, 2], dtype='<u2'), src)
    except OSError:
        pass
    assert not p.exists()
    assert list(tmp_path.glob('*.tmp*')) == []


def test_failed_write_keeps_existing_file(tmp_path, monkeypatch):
    src = _src(tmp_path)
    p = ap.peaks_file_path(src)
    ap.write_peaks_file(p, np.array([1, 2], dtype='<u2'), src)
    monkeypatch.setattr(ap.os, 'replace',
                        lambda *a, **k: (_ for _ in ()).throw(OSError('boom')))
    try:
        ap.write_peaks_file(p, np.array([9], dtype='<u2'), src)
    except OSError:
        pass
    monkeypatch.undo()
    assert np.array_equal(ap.read_peaks_file(p).buckets, [1, 2])
    assert list(tmp_path.glob('*.tmp*')) == []


def test_downsample_max_floors_trailing_partial():
    b = np.array([1, 9, 2, 8, 3, 7, 5], dtype='<u2')
    out = ap.downsample_max(b, 2)
    assert out.dtype == np.dtype('<u2')
    assert np.array_equal(out, np.array([9, 8, 7], dtype='<u2'))


def test_downsample_max_factor_one_and_too_short():
    b = np.array([1, 2, 3], dtype='<u2')
    assert np.array_equal(ap.downsample_max(b, 1), b)
    assert len(ap.downsample_max(b, 5)) == 0


def test_build_peaks_from_decode_bucket_count(tmp_path):
    src = _src(tmp_path)
    buckets = ap.build_peaks_from_decode(src, 2.0)
    assert buckets.dtype == np.dtype('<u2')
    assert abs(len(buckets) - 200) <= 1
    assert buckets.max() > 0


def test_build_peaks_chunk_boundaries_match_single_read(tmp_path, monkeypatch):
    src = _src(tmp_path, seconds=2.0)
    whole = ap.build_peaks_from_decode(src, 2.0)
    monkeypatch.setattr(ap, '_PEAKS_CHUNK_BUCKETS', 7)
    assert np.array_equal(ap.build_peaks_from_decode(src, 2.0), whole)


def test_build_peaks_none_on_bad_input(tmp_path):
    assert ap.build_peaks_from_decode(tmp_path / 'missing.wav', 1.0) is None
    bad = tmp_path / 'bad.mp3'
    bad.write_bytes(b'not audio')
    assert ap.build_peaks_from_decode(bad, 1.0) is None


def test_build_peaks_matches_compute_peaks_10ms(tmp_path):
    src = _src(tmp_path)
    buckets = ap.build_peaks_from_decode(src, 2.0)
    decoded, res = ap.compute_peaks(src, resolution_ms=10)
    assert res == 10
    n = min(len(buckets), len(decoded))
    file_norm = buckets[:n].astype(np.float32) / 32768.0
    assert np.allclose(file_norm, np.array(decoded[:n], dtype=np.float32), atol=1e-6)


def _full_scale_negative_wav(path):
    import wave
    pcm = np.zeros(8000, dtype='<i2')
    pcm[100] = -32768
    with wave.open(str(path), 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(pcm.tobytes())


def test_full_scale_negative_sample_reads_as_peak(tmp_path):
    src = tmp_path / 'neg.wav'
    _full_scale_negative_wav(src)
    decoded, _ = ap.compute_peaks(src, resolution_ms=10)
    assert decoded[1] == 1.0
    assert ap.build_peaks_from_decode(src, 1.0)[1] == 32768
