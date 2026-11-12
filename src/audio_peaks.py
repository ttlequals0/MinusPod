"""Server-side waveform peak generation for the Ad Inbox review modal.

Pipes the requested audio window through ffmpeg → raw 16-bit mono PCM at
8 kHz, then groups into ``resolution_ms`` chunks and emits one peak
(max absolute amplitude, normalized to [0, 1]) per chunk.

A per-episode ``.peaks`` file (10 ms buckets) sits beside the retained
original; this module defines its format and the functions that read, write
and build it.
"""
from __future__ import annotations

import hashlib
import logging
import os
import struct
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from utils.subprocess_registry import tracked_popen, tracked_run
from utils.ffmpeg_run import SAFE_MEDIA_INPUT_ARGS, ffmpeg_timeout

logger = logging.getLogger('podcast.peaks')

PEAKS_SAMPLE_RATE_HZ = 8000 # mono downsample target -- only need amplitude
PEAKS_TIMEOUT_SECONDS = 60
MAX_PEAKS_DURATION_SECONDS = 4 * 60 * 60  # hard ceiling: 4h. Episodes longer
# than that are extreme outliers; refuse rather than spend a minute decoding.
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


class PeaksError(RuntimeError):
    """Raised when ffmpeg fails or the window is invalid."""


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

    if resolution_ms < 1 or resolution_ms > 1000:
        raise PeaksError(f"resolution_ms must be 1-1000, got {resolution_ms}")

    start = max(0.0, float(start_seconds))
    duration = None
    if end_seconds is not None:
        end = float(end_seconds)
        if end <= start:
            raise PeaksError(f"end ({end}) must be > start ({start})")
        duration = end - start
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

    cmd = [
        'ffmpeg', *SAFE_MEDIA_INPUT_ARGS, '-hide_banner', '-loglevel', 'error',
        '-ss', f'{start:.3f}',
        '-i', str(audio_path),
    ]
    if duration is not None:
        cmd += ['-t', f'{duration:.3f}']
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

    samples = np.frombuffer(raw, dtype='<i2')
    samples_per_bucket = max(1, int(PEAKS_SAMPLE_RATE_HZ * resolution_ms / 1000))

    n_buckets = len(samples) // samples_per_bucket
    if n_buckets == 0:
        return [], resolution_ms

    trimmed = samples[: n_buckets * samples_per_bucket].reshape(-1, samples_per_bucket)
    # Peak amplitude per bucket, normalized to [0, 1]. int16 max abs is 32768.
    # max/min instead of abs: abs(-32768) wraps negative in int16
    abs_max = np.maximum(trimmed.max(axis=1).astype(np.int32),
                         -trimmed.min(axis=1).astype(np.int32))
    peaks = abs_max.astype(np.float32) / 32768.0
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


def peaks_file_path(audio_path: Path | str) -> Path:
    """Return the sibling ``.peaks`` path for an audio file."""
    return Path(audio_path).with_suffix('.peaks')


def _sha256_of(path: Path) -> bytes:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        while chunk := f.read(_HASH_CHUNK_BYTES):
            h.update(chunk)
    return h.digest()


def _write_atomic(path: Path, header: bytes, buckets: np.ndarray) -> None:
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=path.name + '.', suffix='.tmp')
    try:
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


def write_peaks_file(path: Path, buckets: np.ndarray, source_path: Path) -> None:
    """Atomically write a peaks file stamped with the source's size, mtime and SHA-256."""
    path = Path(path)
    arr = np.ascontiguousarray(buckets, dtype='<u2')
    if arr.ndim != 1 or len(arr) > _MAX_BUCKET_COUNT:
        raise ValueError('buckets must be a 1-D array of at most 2**32-1 values')
    st = Path(source_path).stat()
    header = _pack_header(len(arr), st.st_size, st.st_mtime_ns, _sha256_of(Path(source_path)))
    _write_atomic(path, header, arr)


def read_peaks_file(path: Path) -> PeaksData | None:
    """Parse a peaks file; None when missing, malformed, or an unsupported format."""
    try:
        with open(path, 'rb') as f:
            head = f.read(_HEADER_SIZE)
            if len(head) != _HEADER_SIZE:
                return None
            magic, version, width, count, size, mtime_ns, sha = _HEADER_STRUCT.unpack(head)
            if magic != PEAKS_MAGIC or version != PEAKS_VERSION or width != PEAKS_BUCKET_WIDTH_MS:
                return None
            if os.fstat(f.fileno()).st_size != _HEADER_SIZE + count * 2:
                return None
            raw = f.read(count * 2)
    except OSError:
        return None
    if len(raw) != count * 2:
        return None
    return PeaksData(version, width, count, size, mtime_ns, sha,
                     np.frombuffer(raw, dtype='<u2'))


def validate_or_restamp_peaks_file(path: Path, source_path: Path) -> bool:
    """True when the file matches the source; restamps the header on a hash match."""
    data = read_peaks_file(path)
    if data is None:
        return False
    try:
        st = Path(source_path).stat()
        if st.st_size == data.source_size and st.st_mtime_ns == data.source_mtime_ns:
            return True
        if st.st_size != data.source_size or _sha256_of(Path(source_path)) != data.source_sha256:
            return False
    except OSError:
        return False
    try:
        header = _pack_header(data.bucket_count, st.st_size, st.st_mtime_ns, data.source_sha256)
        _write_atomic(Path(path), header, data.buckets)
    except OSError as e:
        logger.warning('Could not restamp peaks file %s: %s', Path(path).name, e)
    return True


def downsample_max(buckets: np.ndarray, factor: int) -> np.ndarray:
    """Max of each run of ``factor`` buckets; a trailing partial run is dropped."""
    n = len(buckets) // factor
    if n == 0:
        return np.empty(0, dtype='<u2')
    return buckets[:n * factor].reshape(n, factor).max(axis=1).astype('<u2')


def build_peaks_from_decode(audio_path: Path, duration: float) -> np.ndarray | None:
    """Stream an 8 kHz mono decode and return per-10 ms abs-max as uint16; None on failure."""
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
                        # abs of int16 -32768 wraps; widen first
                        peaks = np.abs(samples.astype(np.int32)).reshape(
                            -1, PEAKS_SAMPLES_PER_BUCKET).max(axis=1)
                        parts.append(np.minimum(peaks, 65535).astype('<u2'))
                returncode = proc.wait()
            finally:
                timer.cancel()
                if proc.poll() is None:
                    proc.kill()
                    proc.wait()
    except OSError as e:
        logger.error('Peaks decode failed: %s', e)
        return None
    if returncode != 0 or not parts:
        logger.error('Peaks decode ffmpeg exited %d for %s', returncode, Path(audio_path).name)
        return None
    return np.concatenate(parts)
