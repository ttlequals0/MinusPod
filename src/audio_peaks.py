"""Server-side waveform peak generation for the Ad Inbox review modal.

Pipes the requested audio window through ffmpeg → raw 16-bit mono PCM at
8 kHz, then groups into ``resolution_ms`` chunks and emits one peak
(max absolute amplitude, normalized to [0, 1]) per chunk.

A per-episode ``.peaks`` file (10 ms buckets) sits beside the retained
original; this module defines its format and the functions that read, write
and build it.
"""
from __future__ import annotations

import fcntl
import hashlib
import logging
import math
import os
import stat
import struct
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

import numpy as np

from utils.episode_paths import peaks_file_path, peaks_lock_path, remove_peaks_temp_files
from utils.ffmpeg_run import SAFE_MEDIA_INPUT_ARGS, ffmpeg_timeout
from utils.bounded_set import BoundedSet
from utils.subprocess_registry import tracked_popen, tracked_run
from utils.ttl_cache import TTLCache

logger = logging.getLogger('podcast.peaks')

PEAKS_SAMPLE_RATE_HZ = 8000 # mono downsample target -- only need amplitude
PEAKS_TIMEOUT_SECONDS = 60
MAX_PEAKS_DURATION_SECONDS = 4 * 60 * 60  # cap on a window decoded per request
PEAKS_INLINE_MAX_SECONDS = MAX_PEAKS_DURATION_SECONDS  # longer originals build in the background
_BUILD_FAILURE_MEMO_SECONDS = 300
_MAX_BACKGROUND_BUILDS = 2
_clock = time.monotonic
MAX_PEAK_BUCKETS = 60_000     # keep JSON payload manageable (~600 KB at 4 dp)

PEAKS_MAGIC = b'MPPK'
PEAKS_VERSION = 1
PEAKS_BUCKET_WIDTH_MS = 10
PEAKS_SAMPLES_PER_BUCKET = PEAKS_SAMPLE_RATE_HZ * PEAKS_BUCKET_WIDTH_MS // 1000
# magic, version, bucket_width_ms, bucket_count, source_size, source_mtime_ns, sha256, pad
_HEADER_STRUCT = struct.Struct('<4sHHIQQ32s4x')
_HEADER_SIZE = _HEADER_STRUCT.size
_MAX_BUCKET_COUNT = 0xFFFFFFFF
_PEAKS_CHUNK_BUCKETS = 60_000  # 600 s of audio, about 9.6 MB per read
_HASH_CHUNK_BYTES = 1 << 20
_STALE_TEMP_SECONDS = 3600
PEAKS_BUILD_WAIT_SECONDS = 30.0  # how long a request waits for another build of the same file
BUILD_OK = 'ok'
BUILD_BUSY = 'busy'
BUILD_FAILED = 'failed'
_HASHED_CACHE_MAX = 256

_GRID_EPSILON = 1e-6
# One value: windows starting before it decode from zero, later ones seek this far ahead and trim
_SEEK_LEAD_IN_SECONDS = 5.0


class PeaksError(RuntimeError):
    """Raised when ffmpeg fails or the window is invalid."""


def abs_max_i16(frames: np.ndarray) -> np.ndarray:
    """Per-row max absolute value of int16 frames, as int32.

    Uses max and min widened first, because abs(-32768) wraps negative in int16.
    """
    return np.maximum(frames.max(axis=1).astype(np.int32), -frames.min(axis=1).astype(np.int32))


def _check_window(start_seconds: float, end_seconds: float | None, resolution_ms: int) -> float:
    """Validate a window request and return its clamped start."""
    if resolution_ms < 1 or resolution_ms > 1000:
        raise PeaksError(f"resolution_ms must be 1-1000, got {resolution_ms}")
    start = max(0.0, float(start_seconds))
    if end_seconds is not None and float(end_seconds) <= start:
        raise PeaksError(f"end ({float(end_seconds)}) must be > start ({start})")
    return start


def compute_peaks(audio_path: Path | str,
                  start_seconds: float = 0.0,
                  end_seconds: float | None = None,
                  resolution_ms: int = 50) -> tuple[list[float], int]:
    """Return ``(peaks, effective_resolution_ms)`` for the window.

    Each peak is in [0, 1] and represents one ``effective_resolution_ms``
    chunk of audio. The effective resolution may be coarser than the
    requested one when the window is so long that honoring the request
    would exceed ``MAX_PEAK_BUCKETS`` (caller learns the true value).

    Args:
        audio_path: Path to the source audio file (any format ffmpeg reads).
        start_seconds: Window start. Negative values are clamped to 0.
        end_seconds: Window end. ``None`` means "to end of file".
        resolution_ms: Width of each peak bucket in milliseconds. Lower is
            higher fidelity / larger response. 20-100ms is the useful range
            for ad-boundary work.

    Raises:
        PeaksError on ffmpeg failure, invalid window, or zero-byte output.
    """
    audio_path = Path(audio_path)
    if not audio_path.exists():
        raise PeaksError(f"audio file not found: {audio_path}")

    start = _check_window(start_seconds, end_seconds, resolution_ms)
    duration = None
    if end_seconds is not None:
        duration = float(end_seconds) - start
        if duration > MAX_PEAKS_DURATION_SECONDS:
            raise PeaksError(
                f"window {duration:.0f}s exceeds {MAX_PEAKS_DURATION_SECONDS}s cap")
        # Auto-scale resolution so the response stays under MAX_PEAK_BUCKETS.
        # The user-supplied resolution_ms is treated as a *minimum fidelity*;
        # we coarsen as needed for very long windows so the JSON doesn't
        # balloon. Wavesurfer renders a couple of pixels per peak, so 60k
        # buckets is plenty even at 20× zoom.
        max_buckets_at_request_res = (duration * 1000) / resolution_ms
        if max_buckets_at_request_res > MAX_PEAK_BUCKETS:
            scaled = int(((duration * 1000) / MAX_PEAK_BUCKETS) + 0.5)
            resolution_ms = min(1000, max(resolution_ms, scaled))

    cmd = ['ffmpeg', *SAFE_MEDIA_INPUT_ARGS, '-hide_banner', '-loglevel', 'error']
    # Near the start decode from zero: a seek there changes AAC priming.
    seek = start - _SEEK_LEAD_IN_SECONDS if start >= _SEEK_LEAD_IN_SECONDS else 0.0
    lead_in = start - seek
    discard = int(round(lead_in * PEAKS_SAMPLE_RATE_HZ))
    if seek > 0:
        cmd += ['-ss', f'{seek:.3f}']
    cmd += ['-i', str(audio_path)]
    if duration is not None:
        cmd += ['-t', f'{duration + lead_in:.3f}']
    cmd += ['-ac', '1', '-ar', str(PEAKS_SAMPLE_RATE_HZ),
            '-f', 's16le', 'pipe:1']

    try:
        proc = tracked_run(cmd, capture_output=True, timeout=PEAKS_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired as e:
        raise PeaksError(f"ffmpeg timed out after {PEAKS_TIMEOUT_SECONDS}s") from e

    if proc.returncode != 0:
        stderr = (proc.stderr or b'').decode('utf-8', errors='replace')[:500]
        raise PeaksError(f"ffmpeg exit {proc.returncode}: {stderr}")

    raw = proc.stdout or b''
    if not raw:
        raise PeaksError("ffmpeg produced no audio data (empty window?)")

    samples = np.frombuffer(raw, dtype='<i2')[discard:]
    if len(samples) == 0:
        raise PeaksError("ffmpeg produced no audio data (empty window?)")
    samples_per_bucket = max(1, int(PEAKS_SAMPLE_RATE_HZ * resolution_ms / 1000))

    n_buckets = len(samples) // samples_per_bucket
    if n_buckets == 0:
        return [], resolution_ms

    trimmed = samples[: n_buckets * samples_per_bucket].reshape(-1, samples_per_bucket)
    # Peak amplitude per bucket, normalized to [0, 1]. int16 max abs is 32768.
    peaks = abs_max_i16(trimmed).astype(np.float32) / 32768.0
    return peaks.tolist(), resolution_ms


@dataclass
class PeaksData:
    version: int
    bucket_width_ms: int
    bucket_count: int
    source_size: int
    source_mtime_ns: int
    source_sha256: bytes
    buckets: np.ndarray


class PeaksSourceChanged(RuntimeError):
    """The source file changed while its peaks were being built or stamped."""


# (peaks path, source size, source mtime_ns, stamped sha) states already hash-validated
_hashed_ok = BoundedSet(_HASHED_CACHE_MAX)


def _remember_hashed(key: tuple) -> bool:
    """Record a hash-validated state; False when it was already recorded."""
    return _hashed_ok.add(key)


def _hash_open_source(source_path: Path) -> tuple[bytes, os.stat_result]:
    """SHA-256 of the source, with the stat of the very handle that was hashed."""
    h = hashlib.sha256()
    with open(source_path, 'rb') as f:
        st = os.fstat(f.fileno())
        while chunk := f.read(_HASH_CHUNK_BYTES):
            h.update(chunk)
    return h.digest(), st


def _write_atomic(path: Path, header: bytes, buckets: np.ndarray, mode: int) -> None:
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=path.name + '.', suffix='.tmp')
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, 'wb') as f:
            f.write(header)
            f.write(buckets.tobytes())
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise


def _pack_header(count: int, size: int, mtime_ns: int, sha: bytes) -> bytes:
    return _HEADER_STRUCT.pack(PEAKS_MAGIC, PEAKS_VERSION, PEAKS_BUCKET_WIDTH_MS,
                               count, size, mtime_ns, sha)


def write_peaks_file(path: Path, buckets: np.ndarray, source_path: Path,
                     expect_stat: tuple[int, int] | None = None) -> None:
    """Atomically write a peaks file stamped with the source's size, mtime and SHA-256.

    ``buckets`` must be uint16. When ``expect_stat`` (size, mtime_ns) is given and the
    hashed source differs, raises PeaksSourceChanged and writes nothing.
    """
    path = Path(path)
    arr = np.asarray(buckets)
    if arr.dtype.kind != 'u' or arr.dtype.itemsize != 2:
        raise ValueError('buckets must be a uint16 array')
    if arr.ndim != 1 or len(arr) > _MAX_BUCKET_COUNT:
        raise ValueError('buckets must be a 1-D array of at most 2**32-1 values')
    sha, st = _hash_open_source(Path(source_path))
    if expect_stat is not None and expect_stat != (st.st_size, st.st_mtime_ns):
        raise PeaksSourceChanged(Path(source_path).name)
    header = _pack_header(len(arr), st.st_size, st.st_mtime_ns, sha)
    _write_atomic(path, header, np.ascontiguousarray(arr, dtype='<u2'), stat.S_IMODE(st.st_mode))
    remove_peaks_temp_files(path, _STALE_TEMP_SECONDS)


def _open_regular(path: Path):
    """Open a regular file for reading without blocking on a FIFO; None otherwise."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            os.close(fd)
            return None
        return os.fdopen(fd, 'rb')
    except OSError:
        return None


def read_peaks_window(path: Path, start: int = 0, stop: int | None = None) -> PeaksData | None:
    """Parse a peaks file and load only buckets [start, stop); None when unusable.

    ``bucket_count`` is the file's total. Bounds are clamped to it. The data bytes
    are read by seek, so a window of a long episode never loads the whole file.
    """
    f = _open_regular(Path(path))
    if f is None:
        return None
    try:
        with f:
            head = f.read(_HEADER_SIZE)
            if len(head) != _HEADER_SIZE:
                return None
            magic, version, width, count, size, mtime_ns, sha = _HEADER_STRUCT.unpack(head)
            if magic != PEAKS_MAGIC or version != PEAKS_VERSION or width != PEAKS_BUCKET_WIDTH_MS:
                return None
            if os.fstat(f.fileno()).st_size != _HEADER_SIZE + count * 2:
                return None
            lo = min(max(0, start), count)
            hi = count if stop is None else min(max(lo, stop), count)
            f.seek(_HEADER_SIZE + lo * 2)
            raw = f.read((hi - lo) * 2)
    except OSError:
        return None
    if len(raw) != (hi - lo) * 2:
        return None
    return PeaksData(version, width, count, size, mtime_ns, sha,
                     np.frombuffer(raw, dtype='<u2'))


def validate_or_restamp_peaks_file(path: Path, source_path: Path) -> bool:
    """True when the file matches the source; restamps the header on a hash match.

    Equal size and mtime are trusted without hashing. Otherwise the source is hashed,
    once per process for each file state, even when the restamp cannot be written.
    """
    path, source_path = Path(path), Path(source_path)
    data = read_peaks_window(path, 0, 0)
    if data is None:
        return False
    try:
        st = source_path.stat()
        if st.st_size == data.source_size and st.st_mtime_ns == data.source_mtime_ns:
            return True
        if st.st_size != data.source_size:
            return False
        key = (str(path), st.st_size, st.st_mtime_ns, data.source_sha256)
        if key in _hashed_ok:
            return True
        sha, st = _hash_open_source(source_path)
    except OSError:
        return False
    if sha != data.source_sha256 or st.st_size != data.source_size:
        return False
    first = _remember_hashed((str(path), st.st_size, st.st_mtime_ns, data.source_sha256))
    full = read_peaks_window(path)
    if full is None:
        return False
    try:
        header = _pack_header(full.bucket_count, st.st_size, st.st_mtime_ns, sha)
        _write_atomic(path, header, full.buckets, stat.S_IMODE(st.st_mode))
    except OSError as e:
        if first:
            logger.warning('Could not restamp peaks file %s: %s', path.name, e)
    return True


def downsample_max(buckets: np.ndarray, factor: int) -> np.ndarray:
    """Max of each run of ``factor`` buckets; a trailing partial run is dropped."""
    if factor < 1:
        raise ValueError('factor must be at least 1')
    n = len(buckets) // factor
    if n == 0:
        return np.empty(0, dtype='<u2')
    return buckets[:n * factor].reshape(n, factor).max(axis=1).astype('<u2')


def build_peaks_from_decode(audio_path: Path, duration: float) -> np.ndarray | None:
    """Stream an 8 kHz mono decode and return per-10 ms abs-max as uint16.

    None on failure and for audio shorter than one bucket (no buckets).
    """
    cmd = ['ffmpeg', *SAFE_MEDIA_INPUT_ARGS, '-v', 'error', '-i', str(audio_path),
           '-ac', '1', '-ar', str(PEAKS_SAMPLE_RATE_HZ), '-f', 's16le', 'pipe:1']
    chunk_bytes = PEAKS_SAMPLES_PER_BUCKET * 2 * _PEAKS_CHUNK_BUCKETS
    bucket_bytes = PEAKS_SAMPLES_PER_BUCKET * 2
    parts: list[np.ndarray] = []
    remainder = b''
    try:
        with tracked_popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                           stdin=subprocess.DEVNULL) as proc:
            timer = threading.Timer(ffmpeg_timeout(duration), proc.kill)
            timer.start()
            try:
                while True:
                    block = proc.stdout.read(chunk_bytes)
                    if not block:
                        break
                    block = remainder + block
                    usable = len(block) // bucket_bytes * bucket_bytes
                    remainder = block[usable:]
                    if usable:
                        samples = np.frombuffer(block, dtype='<i2', count=usable // 2)
                        peaks = abs_max_i16(samples.reshape(-1, PEAKS_SAMPLES_PER_BUCKET))
                        parts.append(peaks.astype('<u2'))
                returncode = proc.wait()
            finally:
                timer.cancel()
                if proc.poll() is None:
                    proc.kill()
                    proc.wait()
    except OSError as e:
        logger.error('Peaks decode failed: %s', e)
        return None
    if returncode != 0:
        logger.error('Peaks decode ffmpeg exited %d for %s', returncode, Path(audio_path).name)
        return None
    if not parts:
        logger.debug('Peaks decode of %s produced no full bucket', Path(audio_path).name)
        return None
    return np.concatenate(parts)


def is_grid_aligned(start_seconds: float, resolution_ms: int) -> bool:
    """True when the request maps onto whole 10 ms file buckets."""
    if resolution_ms < 1 or resolution_ms % PEAKS_BUCKET_WIDTH_MS:
        return False
    cells = max(0.0, float(start_seconds)) * 1000 / PEAKS_BUCKET_WIDTH_MS
    return abs(cells - round(cells)) < _GRID_EPSILON


def compute_peaks_from_file(peaks_path: Path, source_path: Path, start_seconds: float,
                            end_seconds: float | None,
                            resolution_ms: int) -> tuple[list[float], int] | None:
    """Serve a window from the peaks file as ``(peaks, effective_resolution_ms)``.

    None when the request is off the 10 ms grid or the file is missing, stale or
    corrupt. Raises PeaksError for an invalid window or resolution, like compute_peaks.
    Only the bytes of the window are read.
    """
    start = _check_window(start_seconds, end_seconds, resolution_ms)
    if not is_grid_aligned(start, resolution_ms):
        return None
    if not validate_or_restamp_peaks_file(peaks_path, source_path):
        return None
    first, last = _grid_window(start, end_seconds)
    data = read_peaks_window(peaks_path, first, last)
    if data is None:
        return None
    if data.bucket_count == 0:
        return None
    if first >= data.bucket_count:
        raise PeaksError("ffmpeg produced no audio data (empty window?)")
    return _reduce_buckets(data.buckets, resolution_ms)


def _grid_window(start: float, end_seconds: float | None) -> tuple[int, int | None]:
    """First and one-past-last 10 ms bucket index of a grid-aligned window."""
    first = int(round(start * 1000)) // PEAKS_BUCKET_WIDTH_MS
    last = None
    if end_seconds is not None:
        last = int(math.floor(float(end_seconds) * 1000 / PEAKS_BUCKET_WIDTH_MS + _GRID_EPSILON))
    return first, last


def _reduce_buckets(buckets: np.ndarray, resolution_ms: int) -> tuple[list[float], int]:
    """Coarsen 10 ms buckets to the requested width under MAX_PEAK_BUCKETS, normalized to [0, 1]."""
    factor = max(resolution_ms // PEAKS_BUCKET_WIDTH_MS,
                 math.ceil(len(buckets) / MAX_PEAK_BUCKETS))
    coarse = downsample_max(buckets, factor)
    return (coarse.astype(np.float32) / 32768.0).tolist(), factor * PEAKS_BUCKET_WIDTH_MS


def compute_peaks_from_buckets(buckets: np.ndarray, start_seconds: float,
                               end_seconds: float | None,
                               resolution_ms: int) -> tuple[list[float], int] | None:
    """Same answer as compute_peaks_from_file, from whole-episode in-memory buckets."""
    start = _check_window(start_seconds, end_seconds, resolution_ms)
    if not is_grid_aligned(start, resolution_ms):
        return None
    first, last = _grid_window(start, end_seconds)
    if first >= len(buckets):
        raise PeaksError("ffmpeg produced no audio data (empty window?)")
    return _reduce_buckets(buckets[first:last], resolution_ms)


def _pause(seconds: float) -> None:
    threading.Event().wait(seconds)


def _acquire_build_lock(peaks_path: Path, wait_seconds: float) -> int | None:
    """Take the cross-process build lock for a peaks file; None when it stays held."""
    fd = os.open(peaks_lock_path(peaks_path), os.O_CREAT | os.O_RDWR, 0o644)
    deadline = _clock() + wait_seconds
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except BlockingIOError:
            if _clock() >= deadline:
                os.close(fd)
                return None
            _pause(0.05)
        except OSError:
            os.close(fd)
            raise


def _source_stamp(source_path: Path) -> tuple[int, int] | None:
    try:
        st = source_path.stat()
    except OSError:
        return None
    return st.st_size, st.st_mtime_ns


class BuildResult(NamedTuple):
    """Outcome of a locked build; ``buckets`` is set when the decode worked but the write failed."""
    status: str
    buckets: np.ndarray | None = None


def _build_and_write(peaks_path: Path, source_path: Path, duration: float) -> BuildResult:
    """Decode the source and write its peaks file; discards the result if the source changed."""
    st = source_path.stat()
    stamp = (st.st_size, st.st_mtime_ns)
    buckets = build_peaks_from_decode(source_path, duration)
    if buckets is None:
        return BuildResult(BUILD_FAILED)
    try:
        write_peaks_file(peaks_path, buckets, source_path, expect_stat=stamp)
    except OSError as e:
        logger.warning('Peaks build failed for %s: %s', source_path.name, e)
        return BuildResult(BUILD_FAILED, buckets if _source_stamp(source_path) == stamp else None)
    except PeaksSourceChanged:
        logger.info('Peaks build of %s discarded: source changed during decode', source_path.name)
        return BuildResult(BUILD_FAILED)
    # An original removed or replaced after the write: drop the file written for it
    if _source_stamp(source_path) != stamp:
        discard_stale_peaks_file(source_path)
        return BuildResult(BUILD_FAILED)
    return BuildResult(BUILD_OK)


def build_peaks_file_locked(peaks_path: Path, source_path: Path, duration: float,
                            wait_seconds: float = 0.0) -> BuildResult:
    """Build the peaks file under a cross-process lock; never raises.

    BUILD_OK when a valid file exists on return, BUILD_BUSY when another process
    still holds the lock after ``wait_seconds``, BUILD_FAILED otherwise (remembered
    for a while, with the decoded buckets when only the write failed).
    """
    stamp = _source_stamp(Path(source_path))
    result = _build_locked(peaks_path, source_path, duration, wait_seconds)
    _record_build_outcome(str(peaks_path), stamp, result.status)
    return result


def _build_locked(peaks_path, source_path, duration, wait_seconds) -> BuildResult:
    peaks_path, source_path = Path(peaks_path), Path(source_path)
    try:
        fd = _acquire_build_lock(peaks_path, wait_seconds)
    except OSError as e:
        logger.warning('Peaks build lock unavailable for %s: %s', source_path.name, e)
        return BuildResult(BUILD_FAILED)
    if fd is None:
        return BuildResult(BUILD_BUSY)
    try:
        if validate_or_restamp_peaks_file(peaks_path, source_path):
            return BuildResult(BUILD_OK)
        return _build_and_write(peaks_path, source_path, duration)
    except Exception as e:
        logger.warning('Peaks build failed for %s: %s', source_path.name, e)
        return BuildResult(BUILD_FAILED)
    finally:
        os.close(fd)


_inflight: set[str] = set()
_inflight_lock = threading.Lock()
# peaks path -> (source size, source mtime_ns) of a failed build
_build_failures = TTLCache(_BUILD_FAILURE_MEMO_SECONDS, _HASHED_CACHE_MAX, clock=lambda: _clock())


def build_failed_recently(peaks_path: Path, source_path: Path) -> bool:
    """True when a build of this source failed recently and it is unchanged."""
    memo = _build_failures.get(str(peaks_path))
    if memo is None:
        return False
    try:
        st = Path(source_path).stat()
    except OSError:
        return False
    return (st.st_size, st.st_mtime_ns) == memo


def _record_build_outcome(key: str, stamp: tuple[int, int] | None, outcome: str) -> None:
    """Remember a failure against the source stamp taken before the build; a success clears it."""
    if outcome == BUILD_FAILED:
        if stamp is None:
            return
        _build_failures.set(key, stamp)
    elif outcome == BUILD_OK:
        _build_failures.delete(key)


def start_background_build(peaks_path: Path, source_path: Path, duration: float,
                           runner=None, name: str = 'peaks-build') -> threading.Thread | None:
    """Run a build on a daemon thread, one per peaks file per process.

    Returns the thread, or None when this file is already building here or two builds run.
    The runner defaults to build_peaks_file_locked and its errors never escape.
    """
    key = str(peaks_path)
    with _inflight_lock:
        if key in _inflight or len(_inflight) >= _MAX_BACKGROUND_BUILDS:
            return None
        _inflight.add(key)
    run = runner or build_peaks_file_locked

    def _target():
        stamp = _source_stamp(Path(source_path))
        try:
            run(Path(peaks_path), Path(source_path), duration)
        except Exception as e:
            logger.warning('Background peaks build failed for %s: %s', Path(source_path).name, e)
            _record_build_outcome(key, stamp, BUILD_FAILED)
        finally:
            with _inflight_lock:
                _inflight.discard(key)

    thread = threading.Thread(target=_target, name=name, daemon=True)
    try:
        thread.start()
    except RuntimeError:
        with _inflight_lock:
            _inflight.discard(key)
        return None
    return thread


def maybe_write_peaks_file(original_path: Path, peaks_10ms: np.ndarray | None) -> bool:
    """Write or restamp the sibling peaks file from in-memory maxima; False when none given."""
    if peaks_10ms is None or len(peaks_10ms) == 0:
        return False
    path = peaks_file_path(original_path)
    if validate_or_restamp_peaks_file(path, original_path):
        return True
    write_peaks_file(path, np.asarray(peaks_10ms, dtype='<u2'), original_path)
    return True


def discard_stale_peaks_file(original_path: Path) -> bool:
    """Remove the sibling peaks file when it no longer matches its original."""
    path = peaks_file_path(original_path)
    if not path.exists() or validate_or_restamp_peaks_file(path, original_path):
        return False
    path.unlink(missing_ok=True)
    return True
