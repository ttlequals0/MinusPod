"""Transcription using Faster Whisper."""
import contextvars
import io
import json
from datetime import timedelta
import logging
import math
import struct
import tempfile
import os
import re
import subprocess
import threading
import time
import wave
from contextlib import contextmanager

import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

import run_context
from run_context import run_in_worker_thread
from user_agent import download_user_agent
from utils.audio import get_audio_duration, mean_volume_db
from utils.errors import (
    ServiceUnavailableError, AudioTooLargeError, AudioExtractionError,
    AudioExtractionTimeout, LocalTranscriptionUnavailableError, ModelLoadError,
    TranscriptionRejectedError,
)
from utils.text import transcript_gaps
from utils.time import format_vtt_timestamp, parse_iso_utc, utc_now, utc_now_iso
from utils.gpu import (clear_gpu_memory, get_available_memory_gb,
                       get_gpu_device_name, get_gpu_memory_info)
from utils.url import SSRFError
from utils.http import redirect_chain_for_log, safe_url_for_log
from utils.connection_probe import run_probe, parse_probe_json, rejected_detail
from utils.safe_http import (
    URLTrust, safe_get, safe_post, stream_to_file_capped,
    ResponseTooLargeError,
)
from utils.rate_limit import parse_retry_after
from utils.subprocess_registry import tracked_run
from utils.ffmpeg_run import SAFE_MEDIA_INPUT_ARGS
from utils.ttl_cache import TTLCache
from whisper_pool import get_pool
from config import (
    API_CHUNK_DURATION_SECONDS,
    WHISPER_BACKEND_LOCAL,
    WHISPER_BACKEND_API,
    WHISPER_COMPUTE_TYPES,
    WHISPER_COMPUTE_TYPE_DEFAULT,
    WHISPER_COMPUTE_TYPE_FALLBACK_CHAIN,
    resolve_whisper_device,
    CHUNK_OVERLAP_SECONDS,
    CHUNK_MIN_DURATION_SECONDS,
    CHUNK_MAX_DURATION_SECONDS,
    CHUNK_DEFAULT_DURATION_SECONDS,
    MEMORY_SAFETY_MARGIN,
    WHISPER_MEMORY_PROFILES,
    WHISPER_DEFAULT_PROFILE,
    FFMPEG_LONG_TIMEOUT,
    FFMPEG_SHORT_TIMEOUT,
    FFMPEG_CHUNK_TIMEOUT,
    HTTP_MAX_REDIRECTS_API,
    HTTP_MAX_REDIRECTS_FEED,
    HTTP_TIMEOUT_CONNECTION_TEST,
    CONNECTION_TEST_TIMEOUT_CEILING,
    HTTP_TIMEOUT_WHISPER,
    WHISPER_API_TIMEOUT_MIN,
    WHISPER_API_TIMEOUT_MAX,
    coerce_bool_setting,
    get_env_backed_int, MAX_AUDIO_DOWNLOAD_MB_MIN, MAX_AUDIO_DOWNLOAD_MB_ADVISORY,
    log_download_query_enabled,
    HOLE_RETRANSCRIBE_MAX_HOLES, HOLE_RETRANSCRIBE_MAX_SECONDS, HOLE_RETRANSCRIBE_QUIET_DB,
)

# Suppress ONNX Runtime warnings before importing faster_whisper
os.environ.setdefault('ORT_LOG_LEVEL', 'ERROR')

# Set cache directories to writable location (for running as non-root user)
# These must be set BEFORE importing faster_whisper/huggingface
cache_dir = os.environ.get('HF_HOME', '/app/data/.cache')
os.environ.setdefault('HF_HOME', cache_dir)
os.environ.setdefault('HUGGINGFACE_HUB_CACHE', os.path.join(cache_dir, 'hub'))
os.environ.setdefault('XDG_CACHE_HOME', cache_dir)

# Remote-only installs do not ship the local stack; keep the module importable
# so WHISPER_BACKEND=openai-api still works without it (#795).
try:
    import ctranslate2
    from faster_whisper import WhisperModel, BatchedInferencePipeline
    _LOCAL_IMPORT_ERROR: ImportError | None = None
except ImportError as e:
    ctranslate2 = WhisperModel = BatchedInferencePipeline = None
    _LOCAL_IMPORT_ERROR = e

logger = logging.getLogger(__name__)


def _local_unavailable_message() -> str:
    return (
        f"Local Whisper backend needs faster-whisper and ctranslate2 "
        f"({_LOCAL_IMPORT_ERROR}); install them or set WHISPER_BACKEND=openai-api "
        f"with WHISPER_API_BASE_URL and WHISPER_API_KEY"
    )


def local_transcription_available() -> bool:
    return _LOCAL_IMPORT_ERROR is None


def _require_local_transcription() -> None:
    if _LOCAL_IMPORT_ERROR is not None:
        raise LocalTranscriptionUnavailableError(_local_unavailable_message())

# Whisper's encoder window. A clip handed to BatchedInferencePipeline longer
# than this is silently truncated to its first 30s.
WHISPER_CHUNK_SECONDS = 30.0

# check_audio_availability reports failures as prose that main_app.processing's
# retry classifier matches on. Shared so rewording one cannot silently flip the
# other's verdict.
CDN_REFUSED_PREFIX = 'CDN refused'

# Shortest clip worth handing to the pipeline.
MIN_CLIP_SECONDS = 0.1

# Batch size tiers based on audio duration (in seconds)
# Longer episodes need smaller batches to avoid CUDA OOM
BATCH_SIZE_TIERS = [
    (60 * 60, 16),      # < 60 min: batch_size=16
    (90 * 60, 12),      # 60-90 min: batch_size=12
    (120 * 60, 8),      # 90-120 min: batch_size=8
]

# Bounded admission for local (in-process) CUDA transcription. A single GPU
# holds one Whisper model at a time (WhisperModelSingleton); two concurrent
# local transcriptions would each allocate batched-inference memory against
# the same device and OOM each other. This is unrelated to whisper_pool
# (bounded admission for the remote API backend only) and to LLM provider
# budgets/holds: it guards local GPU memory specifically. CPU transcription
# is unaffected (no shared VRAM to protect).
GPU_TRANSCRIBE_MAX_CONCURRENT = max(1, int(os.getenv('GPU_TRANSCRIBE_MAX_CONCURRENT', '1')))
_GPU_ADMISSION_SEMAPHORE = threading.Semaphore(GPU_TRANSCRIBE_MAX_CONCURRENT)


def _gpu_admission_acquire(device: str) -> bool:
    """Take a local-GPU admission permit for `device == 'cuda'`; a no-op
    pass-through otherwise. Returns whether a permit was actually taken, so
    the caller releases only what it acquired."""
    if device != "cuda":
        return False
    _GPU_ADMISSION_SEMAPHORE.acquire()
    return True


def _gpu_admission_release() -> None:
    _GPU_ADMISSION_SEMAPHORE.release()


@contextmanager
def _all_admission_permits():
    """Yield whether every admission permit was taken without blocking; they are released on exit."""
    taken = 0
    try:
        # Holding every permit means no local transcription is running or can start meanwhile.
        while (taken < GPU_TRANSCRIBE_MAX_CONCURRENT
               and _GPU_ADMISSION_SEMAPHORE.acquire(blocking=False)):
            taken += 1
        yield taken == GPU_TRANSCRIBE_MAX_CONCURRENT
    finally:
        for _ in range(taken):
            _GPU_ADMISSION_SEMAPHORE.release()


def unload_whisper_if_idle() -> bool:
    """Unload the local model when no transcription holds an admission permit; True if one was unloaded."""
    with _all_admission_permits() as free:
        if not free or not WhisperModelSingleton.is_loaded():
            return False
        WhisperModelSingleton.unload_model()
        return True


# Last local transcription outcome, mirrored at module scope so
# get_local_transcriber_health() can report it without needing the
# Transcriber() instance (module functions such as probe_whisper_health
# follow the same instance-free pattern for the remote backend).
_local_transcription_state_lock = threading.Lock()
_last_local_transcription_outcome: dict | None = None

# Same outcome persisted as JSON in settings, so the worker answering
# /system/status reports the exhaustion another gunicorn worker recorded.
# Mirrors the batch-ceiling setting's shape (device identity plus a stamp).
LOCAL_OUTCOME_SETTING = 'transcribe_last_local_outcome'


def _record_local_transcription_outcome(outcome: dict) -> None:
    """Keep the last local outcome in-process and in cross-worker state."""
    global _last_local_transcription_outcome
    record = dict(outcome)
    record['backend'] = WHISPER_BACKEND_LOCAL
    if not record.get('device'):
        record['device'] = resolve_whisper_device()
    record['observed_at'] = utc_now_iso()
    with _local_transcription_state_lock:
        _last_local_transcription_outcome = record
    # Inline import: database imports modules that import transcriber.
    from database import Database
    try:
        Database().set_setting(LOCAL_OUTCOME_SETTING, json.dumps(record))
    except Exception as e:
        logger.debug(f"Could not persist local transcription outcome: {e}")


def _read_persisted_local_outcome() -> dict | None:
    """Last local outcome any worker persisted, or None if unreadable."""
    from database import Database
    try:
        raw = Database().get_setting(LOCAL_OUTCOME_SETTING)
    except Exception as e:
        logger.debug(f"Could not read local transcription outcome: {e}")
        return None
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _latest_local_outcome(device: str) -> dict | None:
    """Newest outcome for `device` across this process and shared state.

    A record from another device (the setting changed since) says nothing
    about the device now configured, so it is ignored rather than reported.
    """
    with _local_transcription_state_lock:
        in_process = dict(_last_local_transcription_outcome) if _last_local_transcription_outcome else None
    candidates = [c for c in (in_process, _read_persisted_local_outcome())
                  if c and c.get('device') == device]
    if not candidates:
        return None
    return max(candidates, key=lambda c: c.get('observed_at') or '')


def get_local_transcriber_health() -> dict:
    """Local-backend transcriber health for /system/status.

    The local model runs in-process with no endpoint to probe, so 'available'
    reflects the last transcription outcome (a GPU-OOM exhaustion reads as
    available=False). The outcome is read from shared state so any worker
    reports the latest one; no attempt yet reads as available.
    """
    device = resolve_whisper_device()
    if _LOCAL_IMPORT_ERROR is not None:
        return {
            'backend': WHISPER_BACKEND_LOCAL,
            'device': device,
            'available': False,
            'reason': _local_unavailable_message(),
            'lastOutcome': None,
        }
    outcome = _latest_local_outcome(device)
    last_outcome = None
    if outcome is not None:
        last_outcome = {
            'status': outcome.get('outcome'),
            'device': outcome.get('device'),
            'backend': outcome.get('backend'),
            'observedAt': outcome.get('observed_at'),
        }
    return {
        'backend': WHISPER_BACKEND_LOCAL,
        'device': device,
        'available': outcome is None or outcome.get('outcome') != 'failed',
        'lastOutcome': last_outcome,
    }


def get_remote_transcriber_health() -> dict:
    """API-backend transcriber health for /system/status, from cached probes
    only: a polled status endpoint must not fire an outbound request per
    tick. Unconfigured reads as unavailable; never probed reads as available
    (nothing has failed)."""
    settings = _get_whisper_settings()
    base_url = (settings.get('api_base_url') or '').rstrip('/')
    if not base_url:
        return {'backend': WHISPER_BACKEND_API, 'available': False, 'probed': False}
    probe = _health_cache.get(base_url) or _last_good_health(base_url)
    if probe is None:
        return {'backend': WHISPER_BACKEND_API, 'available': True, 'probed': False}
    return {
        'backend': WHISPER_BACKEND_API,
        'available': bool(probe.get('available')),
        'probed': True,
        'instanceCount': len(probe.get('instances') or []),
    }


def get_transcriber_health() -> dict:
    """Health of the configured backend. The local reading (last in-process
    outcome) means nothing when transcription runs on a remote API."""
    if _get_whisper_settings()['backend'] == WHISPER_BACKEND_API:
        return get_remote_transcriber_health()
    return get_local_transcriber_health()

# Whisper artifacts on silence and music. Matched after trailing punctuation
# is stripped, so a bare phrase or bare punctuation is an artifact.
HALLUCINATION_PATTERNS = re.compile(
    r'^(?:thanks for watching|thank you for watching|please subscribe|'
    r'like and subscribe|see you next time|bye|'
    r'subtitles by the amara\.org community|'
    r'\[music\]|\[applause\]|\[laughter\]|\[silence\]|you)?$',
    re.IGNORECASE
)
TRAILING_PUNCTUATION = '.!?,;:\u2026'


# Filter chain preprocess_audio applies; also folded into chunk extraction
# (extract_audio_chunk(preprocess=True)) so chunked paths run one ffmpeg pass
# per chunk instead of extract + preprocess.
PREPROCESS_AUDIO_FILTERS = 'loudnorm=I=-16:LRA=11:TP=-1.5,highpass=f=80,lowpass=f=8000'


def extract_audio_chunk(
    audio_path: str,
    start_time: float,
    end_time: float,
    preprocess: bool = False,
    flac: bool = False,
) -> str | None:
    """Extract a time range from an audio file using ffmpeg.

    Args:
        audio_path: Path to source audio file
        start_time: Start time in seconds
        end_time: End time in seconds
        preprocess: Apply the same filter chain as preprocess_audio in this
            single pass, so the chunk needs no second ffmpeg pass. Callers
            that set this must skip preprocessing downstream
            (transcribe(..., preprocessed=True)).
        flac: Encode the chunk as FLAC (ready for API upload) instead of
            PCM WAV, skipping the separate encode pass.

    Returns:
        Path to temporary chunk file, or None on failure.
        Caller is responsible for cleaning up the temp file.
    """
    tmp = tempfile.NamedTemporaryFile(suffix='.flac' if flac else '.wav', delete=False)
    output_path = tmp.name
    tmp.close()

    success = False
    try:
        duration = end_time - start_time
        cmd = [
            'ffmpeg', *SAFE_MEDIA_INPUT_ARGS, '-y',
            '-ss', str(start_time),
            '-i', audio_path,
            '-t', str(duration),
            # Ignore embedded cover art (#556). FLAC output can carry
            # pictures, so a malformed APIC frame (ID3 declares PNG, bytes
            # are JPEG) aborts the whole extract without this; WAV output
            # cannot carry pictures, which is why 2.62.0's fold-in of the
            # FLAC encode surfaced it.
            '-vn',
            '-ar', '16000',  # Whisper native sample rate
            '-ac', '1',      # Mono
        ]
        if preprocess:
            cmd += ['-af', PREPROCESS_AUDIO_FILTERS]
        # FLAC for upload; otherwise uncompressed for faster processing
        cmd += ['-c:a', 'flac' if flac else 'pcm_s16le', output_path]

        # With filtering folded in, allow the same budget the standalone
        # preprocess pass had (FFMPEG_LONG_TIMEOUT floor) instead of the
        # tighter extract-only timeout. Scale by chunk length the way every
        # other ffmpeg call site does (#644): chunk size follows available GPU
        # memory, so a flat budget silently fails once chunks grow past it.
        timeout = (FFMPEG_LONG_TIMEOUT if preprocess else FFMPEG_CHUNK_TIMEOUT) + int(duration / 12)
        result = tracked_run(cmd, capture_output=True, timeout=timeout)

        if result.returncode == 0 and os.path.exists(output_path):
            logger.debug(f"Extracted chunk {start_time:.1f}s-{end_time:.1f}s to {output_path}")
            success = True
            return output_path

        logger.warning(
            f"Chunk extraction failed (returncode={result.returncode}): "
            f"{_ffmpeg_error_tail(result.stderr)}"
        )
        return None

    except subprocess.TimeoutExpired:
        logger.warning(
            f"Chunk extraction timed out after {timeout}s for "
            f"{start_time:.1f}s-{end_time:.1f}s ({duration / 60:.1f} min chunk)"
        )
        raise AudioExtractionTimeout(
            f'Audio chunk extraction timed out (ffmpeg exceeded {timeout}s '
            f'preparing a {duration / 60:.1f} min chunk)'
        ) from None
    except Exception as e:
        logger.warning(f"Chunk extraction error: {e}")
        return None
    finally:
        # Clean up temp file on failure (caller cleans up on success)
        if not success:
            _unlink_quiet(output_path)


def merge_overlapping_segments(
    existing_segments: list[dict],
    new_segments: list[dict],
    chunk_start: float,
    overlap_duration: float
) -> list[dict]:
    """Merge new segments into existing, handling overlap deduplication.

    At chunk boundaries, we have overlap_duration seconds of audio that was
    transcribed in both chunks. We need to:
    1. Keep segments from the previous chunk up to the overlap zone
    2. Discard duplicate segments in the overlap zone from the new chunk
    3. Add remaining segments from the new chunk

    Args:
        existing_segments: Segments from previous chunks
        new_segments: Segments from current chunk (timestamps already adjusted)
        chunk_start: Start time of the current chunk in the full audio
        overlap_duration: Duration of overlap in seconds

    Returns:
        Merged list of segments with duplicates removed
    """
    if not existing_segments:
        return new_segments

    if not new_segments:
        return existing_segments

    # The overlap zone is at the beginning of the new chunk
    overlap_end = chunk_start + overlap_duration

    result = existing_segments.copy()

    # Find the last segment end time from existing segments
    # to avoid adding duplicates (existing_segments is non-empty here;
    # the empty case returned early above)
    last_existing_end = max(seg['end'] for seg in existing_segments)

    for seg in new_segments:
        # Skip segments that are entirely in the overlap zone AND
        # have a corresponding segment in existing (based on end time)
        if seg['end'] <= overlap_end:
            # This segment is in the overlap zone
            # Check if there's already a segment covering this time
            overlap_covered = any(
                existing_seg['start'] <= seg['start'] and
                existing_seg['end'] >= seg['end'] - 1.0  # 1s tolerance
                for existing_seg in existing_segments
            )
            if overlap_covered:
                logger.debug("Skipping duplicate segment in overlap zone: %.1f-%.1f", seg['start'], seg['end'])
                continue

        # Skip segments that end before our last known position
        # (they're duplicates from the overlap)
        if seg['end'] <= last_existing_end:
            continue

        # For segments that span the overlap boundary, we keep them
        # since they extend beyond what we have
        result.append(seg)

    return result


def _unlink_quiet(path):
    if path:
        try:
            os.unlink(path)
        except OSError:
            pass


# Chunk extraction (ffmpeg with the normalization filter chain) is CPU-bound
# and takes longer than GPU inference on a chunk, so it runs ahead of the GPU
# in a small pool: while chunk N transcribes, chunks N+1.. extract.
EXTRACT_PREFETCH_WORKERS = 2
# How many chunks past the current one to keep in flight. Bounds /tmp usage:
# each extracted chunk is a 16kHz mono WAV, ~2MB per audio minute.
EXTRACT_PREFETCH_AHEAD = 2


def _chunk_bounds_ahead(start, chunk_duration, duration, overlap, count):
    """Next `count` (start, end_with_overlap) pairs of the chunked loop.

    Single owner of the boundary math: the loop reads its current bounds
    from element 0 and the prefetcher keys its queue on the same pairs.
    """
    bounds = []
    while start < duration and len(bounds) < count:
        end = min(start + chunk_duration, duration)
        end_with_overlap = min(end + overlap, duration) if end < duration else end
        bounds.append((start, end_with_overlap))
        start = end
    return bounds


def _unlink_future_chunk(future):
    """Done-callback for a discarded extraction: swallow its error, drop its file."""
    try:
        _unlink_quiet(future.result())
    except Exception:
        pass


class _ChunkPrefetcher:
    """Runs chunk extractions ahead of the GPU during chunked transcription.

    Futures are keyed by (start, end_with_overlap) from _chunk_bounds_ahead,
    the same helper the chunk loop reads its own bounds from. When the chunk
    size changes mid-run (OOM shrink, extraction-timeout shrink) the next
    take() computes bounds the queue does not hold, and the whole stale
    queue is discarded. Discarded and leftover extractions unlink their
    temp files on completion. Single-threaded caller; no locking needed.
    """

    def __init__(self, audio_path):
        self._audio_path = audio_path
        self._executor = ThreadPoolExecutor(max_workers=EXTRACT_PREFETCH_WORKERS)
        self._pending = {}

    def _submit(self, bounds):
        if bounds not in self._pending:
            start, end = bounds
            # run_in_worker_thread keeps the pool thread's ffmpeg log lines
            # inside the episode's run log.
            self._pending[bounds] = self._executor.submit(
                run_in_worker_thread(extract_audio_chunk),
                self._audio_path, start, end, preprocess=True,
            )

    def _queue(self, start, chunk_duration, duration, overlap):
        """Queue the chunk at `start` plus the lookahead; return its bounds.

        Returns None past the end of the audio (a zero-length file reaches
        prime() this way); only prime() may pass such a start.
        """
        bounds = _chunk_bounds_ahead(
            start, chunk_duration, duration, overlap, 1 + EXTRACT_PREFETCH_AHEAD,
        )
        if not bounds:
            return None
        if bounds[0] not in self._pending:
            self._discard_pending()
        for each in bounds:
            self._submit(each)
        return bounds[0]

    def prime(self, start, chunk_duration, duration, overlap):
        """Start extracting without blocking, so callers can overlap other
        setup work (model load) with the first chunk's ffmpeg pass."""
        self._queue(start, chunk_duration, duration, overlap)

    def take(self, start, chunk_duration, duration, overlap):
        """Blocking path for the chunk at `start`, extracting now if needed.

        Raises whatever the extraction raised (AudioExtractionTimeout
        included), exactly like calling extract_audio_chunk inline.
        """
        return self._pending.pop(
            self._queue(start, chunk_duration, duration, overlap)
        ).result()

    def _discard_pending(self):
        for future in self._pending.values():
            if not future.cancel():
                future.add_done_callback(_unlink_future_chunk)
        self._pending.clear()

    def close(self):
        self._discard_pending()
        self._executor.shutdown(wait=False)


def _ffmpeg_error_tail(stderr, limit: int = 500) -> str:
    """The diagnosable part of ffmpeg stderr for logs.

    A head-truncated slice is consumed entirely by the version banner and
    configuration line (#556 was undiagnosable from logs because of it), so
    surface lines that look like errors, falling back to the tail.
    """
    if not stderr:
        return 'no error output'
    text = stderr.decode('utf-8', errors='replace')
    error_lines = [ln for ln in text.splitlines()
                   if re.search(r'error|invalid|fail|denied|no such',
                                ln, re.IGNORECASE)]
    picked = '\n'.join(error_lines[-5:]) if error_lines else text
    return picked[-limit:]


def _max_download_mb() -> int:
    """Episode download cap in MB (#493). Env-backed: the UI value wins,
    the env var MAX_AUDIO_DOWNLOAD_MB seeds the default at boot. No hard
    ceiling -- deployments deliberately above 10 GB keep working -- but the
    cap is the disk-fill guard, so oversized values leave a trace.
    """
    mb = get_env_backed_int('max_audio_download_mb',
                            floor=MAX_AUDIO_DOWNLOAD_MB_MIN)
    if mb > MAX_AUDIO_DOWNLOAD_MB_ADVISORY:
        logger.warning(
            f"max_audio_download_mb={mb} is over 10GB; the download cap is "
            f"the disk-fill guard, check for a typo")
    return mb


def _get_whisper_settings() -> dict[str, str]:
    """Read all whisper backend settings from DB with env var fallbacks.

    Returns a dict with keys: backend, api_base_url, api_key, api_model,
    language. Does NOT include compute_type, which is read separately via
    _get_whisper_compute_type to keep its value out of any dict that also
    holds api_key (CodeQL py/clear-text-logging-sensitive-data).
    """
    defaults = {
        'backend': os.environ.get('WHISPER_BACKEND', WHISPER_BACKEND_LOCAL),
        'api_base_url': os.environ.get('WHISPER_API_BASE_URL', ''),
        'api_key': os.environ.get('WHISPER_API_KEY', ''),
        'api_model': os.environ.get('WHISPER_API_MODEL', 'whisper-1'),
        'language': os.environ.get('WHISPER_LANGUAGE') or 'en',
        'skip_flac_compression': coerce_bool_setting(os.environ.get('SKIP_FLAC_COMPRESSION', 'false')),
        'api_timeout': _clamp_api_timeout(
            os.environ.get('WHISPER_API_TIMEOUT') or HTTP_TIMEOUT_WHISPER),
        'max_attempts': 2,
        'is_failover': False,
    }
    try:
        # Inline import: Database depends on modules that import transcriber,
        # causing a circular import if placed at module level.
        from database import Database
        db = Database()
        for setting_key, default_key in [
            ('whisper_backend', 'backend'),
            ('whisper_api_base_url', 'api_base_url'),
            ('whisper_api_key', 'api_key'),
            ('whisper_api_model', 'api_model'),
            ('whisper_language', 'language'),
        ]:
            if setting_key == 'whisper_api_key':
                val = db.get_secret(setting_key)
            else:
                val = db.get_setting(setting_key)
            if val:
                defaults[default_key] = val

        skip_flac_raw = db.get_setting('skip_flac_compression')
        if skip_flac_raw is not None:
            defaults['skip_flac_compression'] = coerce_bool_setting(skip_flac_raw)

        defaults['api_timeout'] = _clamp_api_timeout(db.get_setting_float(
            'whisper_api_timeout_seconds', defaults['api_timeout']))
        defaults['max_attempts'] = db.get_setting_int(
            'whisper_max_attempts', defaults['max_attempts'])
    except Exception as e:
        logger.warning(f"Could not read whisper settings from DB, using env defaults: {e}")

    return defaults


def _get_failover_whisper_settings() -> dict[str, str]:
    """Failover whisper config, read from failover_whisper_* settings (#806).
    Same shape as _get_whisper_settings plus 'local_model' and 'is_failover': True;
    blank language/max_attempts fall back to the active config's values."""
    active = _get_whisper_settings()
    defaults = {
        'backend': WHISPER_BACKEND_API,
        'api_base_url': '',
        'api_key': '',
        'api_model': 'whisper-1',
        'language': active['language'],
        'skip_flac_compression': active['skip_flac_compression'],
        'api_timeout': active['api_timeout'],
        'max_attempts': active['max_attempts'],
        'local_model': '',
        'is_failover': True,
    }
    try:
        # Inline import: see _get_whisper_settings above, Database would be a
        # circular import at module level.
        from database import Database
        db = Database()
        backend = db.get_setting('failover_whisper_backend')
        if backend:
            defaults['backend'] = backend
        defaults['local_model'] = db.get_setting('failover_whisper_model') or ''
        base_url = db.get_setting('failover_whisper_api_base_url')
        if base_url:
            defaults['api_base_url'] = base_url
        api_key = db.get_secret('failover_whisper_api_key')
        if api_key:
            defaults['api_key'] = api_key
        api_model = db.get_setting('failover_whisper_api_model')
        if api_model:
            defaults['api_model'] = api_model
        language = db.get_setting('failover_whisper_language')
        if language:
            defaults['language'] = language

        defaults['api_timeout'] = _clamp_api_timeout(db.get_setting_float(
            'failover_whisper_api_timeout_seconds', defaults['api_timeout']))
        defaults['max_attempts'] = db.get_setting_int(
            'whisper_max_attempts', defaults['max_attempts'])
    except Exception as e:
        logger.warning(f"Could not read failover whisper settings from DB, using defaults: {e}")

    return defaults


def active_whisper_settings() -> dict[str, str]:
    """Whisper settings to use right now (#806): the failover config while
    whisper failover is active, otherwise the primary config."""
    try:
        # Inline import: failover imports database at module top, which would
        # cycle back to transcriber at module load time (same reason Database
        # is imported inline throughout this file).
        import failover
        if failover.is_active(failover.TARGET_WHISPER):
            return _get_failover_whisper_settings()
    except Exception as e:
        logger.warning(f"Could not check whisper failover state: {e}")
    return _get_whisper_settings()


def _clamp_api_timeout(value) -> float:
    """Coerce and clamp a Whisper request timeout (#593).

    Env vars and direct DB writes skip the API validator, so the range is
    enforced here as well; an unusable value falls back to the shipped default.
    """
    try:
        return float(min(WHISPER_API_TIMEOUT_MAX,
                         max(WHISPER_API_TIMEOUT_MIN, float(value))))
    except (TypeError, ValueError):
        return float(HTTP_TIMEOUT_WHISPER)


def _api_timeout(whisper_settings: dict) -> float:
    """Per-request Whisper upload timeout from a resolved settings dict."""
    return _clamp_api_timeout(whisper_settings.get('api_timeout'))


def _connection_test_timeout(whisper_settings: dict = None) -> float:
    """How long the Settings test-connection probe waits.

    A backend slow enough to need a raised request timeout can also be slow to
    cold-load a model, so the probe follows that setting rather than failing
    while transcription works. Capped so a hung backend cannot hold the
    settings page for the full transcription timeout.
    """
    settings = whisper_settings if whisper_settings is not None else _get_whisper_settings()
    return max(HTTP_TIMEOUT_CONNECTION_TEST,
               min(_api_timeout(settings), CONNECTION_TEST_TIMEOUT_CEILING))


def check_whisper_connectivity(timeout: float = 5.0) -> bool:
    """Availability probe for the offline queue re-drive (#482).

    Local backend (or an unconfigured API URL) never blocks a re-drive: those
    failure modes are not connectivity. For the API backend, any HTTP response
    with status below 500 proves the endpoint is up (401/404 included).
    """
    settings = _get_whisper_settings()
    if settings['backend'] != WHISPER_BACKEND_API or not settings['api_base_url']:
        return True
    url = f"{settings['api_base_url'].rstrip('/')}/models"
    headers = {}
    if settings['api_key']:
        headers['Authorization'] = f"Bearer {settings['api_key']}"
    try:
        response = safe_get(
            url,
            trust=URLTrust.OPERATOR_CONFIGURED,
            timeout=timeout,
            headers=headers,
        )
        return response.status_code < 500
    except Exception as e:
        logger.debug(f"Whisper connectivity probe failed: {e}")
        return False


def _trigger_whisper_failover(original: Exception) -> None:
    """Trigger whisper failover; re-raise `original` when no failover ends up active, so the episode defers."""
    import failover  # inline: see active_whisper_settings for the cycle reason
    target = failover.TARGET_WHISPER
    if not failover.trigger(target, str(original)) and not failover.is_active(target):
        raise original


def _note_whisper_settings(whisper_settings: dict) -> None:
    """Mark the current run as having used the failover transcriber."""
    if whisper_settings.get('is_failover'):
        ctx = run_context.current()
        if ctx is not None:
            ctx.whisper_failover_used = True


def is_whisper_failover_trigger(exc: Exception) -> bool:
    """Errors that mean this transcriber config cannot serve the episode right now."""
    return isinstance(exc, (ServiceUnavailableError, TranscriptionRejectedError,
                            ModelLoadError, LocalTranscriptionUnavailableError))


# Local-backend model override while running on the failover config (#806);
# consulted by WhisperModelSingleton.get_configured_model() ahead of the DB
# setting. Set only around a local transcription that uses failover settings.
_local_model_override: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    'whisper_local_model_override', default=None)

# Sentinel: a chunk plan's failure budget was blown with no failover switch
# available, so the caller aborts with no transcript (#806).
_ABORT_CHUNK_PLAN = object()


_HEALTH_INSTANCE_FIELDS = (
    'model', 'device', 'compute_type', 'batch_size', 'max_concurrent', 'vad_filter',
)

# Cached probe results, keyed by base URL, so a 15s settings-page poll loop
# does not fire fresh outbound requests every tick.
_HEALTH_CACHE_TTL_SECONDS = 120.0
_health_cache = TTLCache(ttl_seconds=_HEALTH_CACHE_TTL_SECONDS)
# The budget caps how many samples a probe starts; the in-flight set stops a
# 15s poll loop stacking more against a backend still being probed.
_HEALTH_PROBE_BUDGET_SECONDS = 15.0
_health_inflight: set[str] = set()
_health_inflight_lock = threading.Lock()
# Last successful probe per base URL, kept past the cache TTL only to answer
# a caller that arrives while that URL's probe is still running.
_health_last_good: dict[str, tuple[float, dict]] = {}
_HEALTH_LAST_GOOD_MAX = 32
_HEALTH_LAST_GOOD_MAX_AGE_SECONDS = 3 * _HEALTH_CACHE_TTL_SECONDS


def _last_good_health(cache_key: str) -> dict | None:
    """Last successful probe for a backend, if recent enough to still show."""
    entry = _health_last_good.get(cache_key)
    if entry is None:
        return None
    stamped_at, result = entry
    if time.monotonic() - stamped_at > _HEALTH_LAST_GOOD_MAX_AGE_SECONDS:
        return None
    return result


def _remember_health(cache_key: str, result: dict) -> None:
    """Record a successful probe, evicting the oldest entry when full."""
    entries = list(_health_last_good.items())
    if len(entries) >= _HEALTH_LAST_GOOD_MAX and cache_key not in _health_last_good:
        _health_last_good.pop(min(entries, key=lambda kv: kv[1][0])[0], None)
    _health_last_good[cache_key] = (time.monotonic(), result)


def _coerce_hashable(value):
    """Stringify a list/dict health field so the mismatch set comprehension stays hashable."""
    return str(value) if isinstance(value, (list, dict)) else value


def _clamped_concurrency(value) -> int:
    """One instance's usable max_concurrent, floored at 1.

    Accepts the int, float, and numeric-string spellings backends use; bool
    and anything unparseable count as 1.
    """
    if isinstance(value, bool):
        return 1
    try:
        return max(1, int(float(value)))
    except (TypeError, ValueError, OverflowError):
        return 1


def probe_whisper_health(base_url: str | None = None, samples: int = 8,
                         timeout: float = 5.0, api_key: str | None = None,
                         refresh: bool = False,
                         budget: float = _HEALTH_PROBE_BUDGET_SECONDS) -> dict:
    """Sample a self-hosted Whisper backend's optional /health endpoint.

    Behind a load balancer, repeated calls round-robin across replicas,
    revealing the replica count and total concurrency accepted. Stops early
    after three consecutive already-seen instances. Results are cached per
    base URL for 120s; refresh=True skips that read and re-probes, then
    stores the fresh result so an on-demand check such as the connection
    test also clears a stale entry. One probe runs per backend at a time,
    and a caller arriving mid-probe (refresh included) gets the cached or
    last good result instead of queuing. `budget` caps how many further
    samples start, not the wall time of one already in flight. Never
    raises: an unusable sample is skipped, and a probe that finds nothing
    returns {'available': False}.
    """
    settings = None
    if base_url is None:
        settings = _get_whisper_settings()
        base_url = settings['api_base_url']
    if not base_url:
        return {'available': False}
    if api_key is None:
        api_key = settings['api_key'] if settings else ''

    cache_key = base_url.rstrip('/')
    if not refresh:
        cached = _health_cache.get(cache_key)
        if cached is not None:
            return cached

    # Per backend, so a test against a new URL is never blocked by a probe
    # hanging on the old one.
    with _health_inflight_lock:
        busy = cache_key in _health_inflight
        if not busy:
            _health_inflight.add(cache_key)
    if busy:
        # A non-refresh caller only gets here after its own cache read missed,
        # so fall back to the last good probe rather than blanking the panel
        # for as long as the running probe takes.
        return (_health_cache.get(cache_key) or _last_good_health(cache_key)
                or {'available': False})
    try:
        return _probe_whisper_health(cache_key, samples, timeout, api_key, refresh, budget)
    finally:
        with _health_inflight_lock:
            _health_inflight.discard(cache_key)


def _probe_whisper_health(cache_key: str, samples: int, timeout: float,
                          api_key: str | None, refresh: bool, budget: float) -> dict:
    """probe_whisper_health's body, run with this backend marked in flight."""
    url = f"{cache_key}/health"
    headers = _bearer_headers(api_key)
    give_up_at = time.monotonic() + budget

    bodies: dict[str, dict] = {}
    repeat_streak = 0
    converged = False
    for attempt in range(max(1, samples)):
        if attempt and time.monotonic() >= give_up_at:
            break
        try:
            response = safe_get(url, trust=URLTrust.OPERATOR_CONFIGURED,
                                timeout=timeout, headers=headers)
            if response.status_code != 200:
                continue
            body = response.json()
        except Exception as e:
            logger.debug(f"Whisper health probe failed: {e}")
            continue
        if not isinstance(body, dict) or not isinstance(body.get('instance'), str):
            continue

        instance = body['instance']
        if instance in bodies:
            repeat_streak += 1
        else:
            repeat_streak = 0
            bodies[instance] = body
        if repeat_streak >= 3:
            converged = True
            break

    if not bodies:
        # On a refresh the cache may hold a good probe; one failed on-demand
        # check is not reason enough to blank the panel for 120s.
        if not refresh:
            _health_cache.set(cache_key, {'available': False})
        return {'available': False}

    instances = [
        {'instance': inst,
         **{f: _coerce_hashable(bodies[inst].get(f)) for f in _HEALTH_INSTANCE_FIELDS}}
        for inst in bodies
    ]
    suggested = sum(_clamped_concurrency(inst['max_concurrent']) for inst in instances)
    mismatch = [
        field for field in ('model', 'compute_type', 'device')
        if len({inst[field] for inst in instances}) > 1
    ]

    result = {
        'available': True,
        'instances': instances,
        'suggested_max_requests': suggested,
        'mismatch': mismatch,
        # Sampling ran out before an instance repeated three times running,
        # so it never demonstrably wrapped the replica set: the count is a
        # lower bound. A balancer that is not round robin lands here.
        'sampled_floor': not converged,
    }
    _health_cache.set(cache_key, result)
    _remember_health(cache_key, result)
    return result


def _transcription_url(base_url: str) -> str:
    """Endpoint URL for an OpenAI-compatible transcription request. Shared
    by the real upload path and the connection probe so they cannot drift."""
    return f"{base_url.rstrip('/')}/audio/transcriptions"


def _bearer_headers(api_key: str) -> dict[str, str]:
    """Auth headers for the whisper API. Shared by the real upload path and
    the connection probe."""
    return {'Authorization': f'Bearer {api_key}'} if api_key else {}


def _attach_top_level_words(segments, words) -> None:
    """Fold an OpenAI verbose_json top-level `words` array into its segments.

    Spec-compliant servers return words top-level, not nested; place each in
    the segment nearest its midpoint so boundary refinement finds them."""
    if not segments or not words:
        return
    if any(seg.get('words') for seg in segments):
        return
    for w in words:
        start, end = w.get('start'), w.get('end')
        if start is None or end is None:
            continue
        mid = (start + end) / 2
        target = next(
            (s for s in segments if s['start'] <= mid <= s['end']), None)
        if target is None:
            target = min(segments, key=lambda s: min(
                abs(s['start'] - mid), abs(s['end'] - mid)))
        target.setdefault('words', []).append(
            {'word': w.get('word', ''), 'start': start, 'end': end})
    # A server may return the array unsorted; refinement scans words in order.
    for s in segments:
        if s.get('words'):
            s['words'].sort(key=lambda x: x['start'])


def _warn_if_word_timestamps_missing(segments, whisper_settings) -> None:
    """Warn once when a transcription that asked for word timestamps got none.
    A 4xx on the word granularity is already reported; a 200 that simply omits the
    words silently disabled boundary refinement."""
    if not segments or any(seg.get('words') for seg in segments):
        return
    logger.warning(
        "Whisper API returned no word timestamps (provider=%s model=%s); "
        "boundary refinement will be skipped for this transcription",
        safe_url_for_log(whisper_settings.get('api_base_url') or ''),
        whisper_settings.get('api_model'),
    )


def _probe_wav_bytes(duration_s: float = 1.0, rate: int = 16000) -> bytes:
    """One second of 440 Hz tone as an in-memory WAV (mono, 16-bit).

    Used as the upload for probe_transcription_endpoint. A tone rather than silence:
    some servers reject audio whose signal level is effectively zero before
    the request ever reaches the transcription path.
    """
    n = int(duration_s * rate)
    samples = [int(8000 * math.sin(2 * math.pi * 440 * i / rate)) for i in range(n)]
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(struct.pack(f'<{n}h', *samples))
    return buf.getvalue()


def _probe_upload(skip_flac_compression: bool) -> tuple[str, bytes]:
    """Probe audio in the format a real episode upload would use: FLAC by
    default, WAV when the skip toggle is on (mirrors _transcribe_via_api,
    including its fall-back to WAV when the FLAC encode fails). Matters for
    servers with no FLAC decoder (OVMS): a WAV-only probe would pass while
    real episodes fail.
    """
    wav = _probe_wav_bytes()
    if skip_flac_compression:
        return 'probe.wav', wav
    wav_path = flac_path = None
    try:
        fd, wav_path = tempfile.mkstemp(suffix='.wav')
        with os.fdopen(fd, 'wb') as fh:
            fh.write(wav)
        fd, flac_path = tempfile.mkstemp(suffix='.flac')
        os.close(fd)
        encode = tracked_run(
            ['ffmpeg', *SAFE_MEDIA_INPUT_ARGS, '-y', '-i', wav_path, '-c:a', 'flac', flac_path],
            capture_output=True, timeout=FFMPEG_SHORT_TIMEOUT,
        )
        if encode.returncode == 0 and os.path.getsize(flac_path) > 0:
            with open(flac_path, 'rb') as fh:
                return 'probe.flac', fh.read()
    except Exception as e:
        logger.warning(f"Probe FLAC encode failed, sending WAV: {e}")
    finally:
        _unlink_quiet(wav_path)
        _unlink_quiet(flac_path)
    return 'probe.wav', wav


def probe_transcription_endpoint(base_url: str, api_key: str = '',
                                 model: str = 'whisper-1',
                                 skip_flac_compression: bool = True) -> dict:
    """End-to-end reachability test for a remote transcription endpoint (#544).

    Uploads one second of generated audio using the same URL construction,
    auth header, form fields, and upload format as the real transcription
    path, so the result reflects what an actual episode upload would hit.
    Distinguishes the failure classes that matter for setup debugging:
    server unreachable, server alive but wrong endpoint path (OVMS answers
    400/404 everywhere except its versioned base), and endpoint present but
    rejecting requests.

    Returns a dict: ok (transcription endpoint accepted the probe),
    reachable (server responded at all), status (HTTP code when one was
    received), detail (user-facing outcome message).
    """
    url = _transcription_url(base_url)
    headers = _bearer_headers(api_key)
    form_data = {
        'model': model,
        'response_format': 'verbose_json',
        # Segment-only granularity: some servers (OVMS, older
        # faster-whisper-server) reject 'word' outright, and this test is
        # about connectivity, not feature support.
        'timestamp_granularities[]': ['segment'],
    }
    filename, audio = _probe_upload(skip_flac_compression)
    probe_timeout = _connection_test_timeout()
    error, status, body_bytes = run_probe(
        lambda: safe_post(
            url,
            trust=URLTrust.OPERATOR_CONFIGURED,
            timeout=probe_timeout,
            max_redirects=HTTP_MAX_REDIRECTS_API,
            files={'file': (filename, io.BytesIO(audio))},
            data=form_data,
            headers=headers,
            stream=True,
        ),
        probe_timeout,
        log_context=safe_url_for_log(url),
        slow_hint='A first request can be slow while the model loads; '
                  'try again in a minute.',
    )
    if error:
        return error

    result = {'ok': False, 'reachable': True, 'status': status}
    if status < 400:
        body = parse_probe_json(body_bytes)
        if isinstance(body, dict):
            result['ok'] = True
            result['detail'] = (f'Connected. The transcription endpoint '
                                f'accepted the test audio (HTTP {status}).')
        else:
            result['detail'] = (f'The server answered HTTP {status} but did '
                                'not return a transcription result. Check '
                                'that the URL points at an OpenAI-compatible '
                                'transcription service.')
    elif status in (401, 403):
        if api_key:
            result['detail'] = (f'The server rejected the saved API key '
                                f'(HTTP {status}). Check the key.')
        else:
            result['detail'] = (f'The endpoint requires an API key '
                                f'(HTTP {status}). The test sends the saved '
                                'key, and only when the tested URL matches '
                                'the saved one -- save your key and base '
                                'URL, then test again.')
    elif status == 404:
        result['detail'] = ('The server is running, but there is no '
                            'transcription endpoint at this path (HTTP 404). '
                            'Check the path in the base URL -- for example, '
                            'OpenVINO Model Server uses /v3.')
    else:
        result['detail'] = rejected_detail(status, body_bytes)
    return result


def _get_chunk_settings() -> dict[str, int]:
    """Read chunked-transcription tuning settings from DB with defaults.

    Returns dict with keys: max_chunk_seconds, concurrent_chunks,
    chunk_overlap_seconds. All ints. Used by the parallel API path to
    size chunks per backend (e.g. 600 for Whisper, 28 for Parakeet).
    """
    defaults: dict[str, int] = {
        'max_chunk_seconds': API_CHUNK_DURATION_SECONDS,
        'concurrent_chunks': 4,
        'chunk_overlap_seconds': CHUNK_OVERLAP_SECONDS,
    }
    try:
        from database import Database
        db = Database()
        for setting_key, default_key in [
            ('transcribe_max_chunk_seconds', 'max_chunk_seconds'),
            ('transcribe_concurrent_chunks', 'concurrent_chunks'),
            ('transcribe_chunk_overlap_seconds', 'chunk_overlap_seconds'),
        ]:
            val = db.get_setting(setting_key)
            if val:
                try:
                    defaults[default_key] = max(1, int(val))
                except (ValueError, TypeError):
                    logger.warning(f"Invalid {setting_key}={val!r}, using default")
    except Exception as e:
        logger.warning(f"Could not read chunk settings from DB, using defaults: {e}")

    # Clamp to the same bounds the API validator enforces. Values set via env
    # var or a direct DB write skip that validator, so guard at the point of
    # use: an unbounded concurrency would thread-bomb the API, and an overlap
    # >= chunk size makes merge_overlapping_segments over-dedupe and drop real
    # transcript content.
    defaults['max_chunk_seconds'] = min(7200, defaults['max_chunk_seconds'])
    defaults['concurrent_chunks'] = min(32, defaults['concurrent_chunks'])
    defaults['chunk_overlap_seconds'] = min(
        defaults['chunk_overlap_seconds'],
        max(1, defaults['max_chunk_seconds'] - 1),
    )
    return defaults


def _log_prefix() -> str:
    """"[slug:episode_id] " for the calling run, else empty."""
    ctx = run_context.current()
    return f"[{ctx.key}] " if ctx else ''


# Canonical lookup: keys are what we accept, values are the literal constants
# from WHISPER_COMPUTE_TYPES. Looking up untrusted input and returning its
# canonical form re-sources the value from this module-level literal, so any
# downstream log of the returned value is references a known-safe constant
# (CodeQL py/clear-text-logging-sensitive-data).
_CANONICAL_COMPUTE_TYPES = {t: t for t in WHISPER_COMPUTE_TYPES}


def _get_whisper_compute_type() -> str:
    """Read WHISPER_COMPUTE_TYPE (DB preferred, env fallback), validated."""
    raw = os.environ.get('WHISPER_COMPUTE_TYPE', WHISPER_COMPUTE_TYPE_DEFAULT)
    try:
        from database import Database
        db_value = Database().get_setting('whisper_compute_type')
        if db_value:
            raw = db_value
    except Exception as e:
        logger.warning(f"Could not read whisper_compute_type from DB: {e}")

    canonical = _CANONICAL_COMPUTE_TYPES.get(raw)
    if canonical is None:
        logger.warning(
            f"Unknown WHISPER_COMPUTE_TYPE; falling back to "
            f"{WHISPER_COMPUTE_TYPE_DEFAULT!r}. Allowed: {WHISPER_COMPUTE_TYPES}"
        )
        return WHISPER_COMPUTE_TYPE_DEFAULT
    return canonical


def calculate_optimal_chunk_duration(
    model_name: str,
    device: str = "cuda",
) -> tuple[int, str]:
    """Calculate optimal chunk duration based on available memory and model size.

    Uses model-specific memory profiles and current available memory to
    determine how much audio can be safely processed in one chunk. Only the
    local backend reaches this path; the API backend branches to
    _transcribe_chunked_parallel_api before chunk sizing.

    Args:
        model_name: Whisper model name (e.g., "small", "large-v3")
        device: "cuda" or "cpu"

    Returns:
        Tuple of (chunk_duration_seconds, reasoning_message)
    """
    # Get model memory profile
    profile = WHISPER_MEMORY_PROFILES.get(model_name, WHISPER_DEFAULT_PROFILE)
    base_memory_gb, memory_per_minute_gb = profile

    # Get available memory
    available_gb, memory_type = get_available_memory_gb(device)

    if available_gb is None:
        logger.warning("Could not determine available memory, using default chunk size")
        return CHUNK_DEFAULT_DURATION_SECONDS, "memory detection failed, using default"

    # Apply safety margin
    usable_gb = available_gb * MEMORY_SAFETY_MARGIN

    # Calculate how much memory is available for audio processing
    # (total usable minus base model memory)
    available_for_audio_gb = usable_gb - base_memory_gb

    if available_for_audio_gb <= 0:
        # Not enough memory even for base model - use minimum chunk size
        logger.warning(
            f"Available memory ({available_gb:.1f}GB) barely covers model base "
            f"({base_memory_gb:.1f}GB), using minimum chunk size"
        )
        return CHUNK_MIN_DURATION_SECONDS, f"low memory ({available_gb:.1f}GB {memory_type})"

    # Calculate max duration that fits in available memory
    # available_for_audio = duration_minutes * memory_per_minute
    # duration_minutes = available_for_audio / memory_per_minute
    max_duration_minutes = available_for_audio_gb / memory_per_minute_gb
    max_duration_seconds = int(max_duration_minutes * 60)

    # Clamp to configured min/max
    chunk_duration = max(
        CHUNK_MIN_DURATION_SECONDS,
        min(max_duration_seconds, CHUNK_MAX_DURATION_SECONDS)
    )

    reason = (
        f"{available_gb:.1f}GB {memory_type} available, "
        f"model '{model_name}' ({base_memory_gb:.1f}GB base + {memory_per_minute_gb*1000:.0f}MB/min)"
    )

    logger.info(
        f"Calculated chunk duration: {chunk_duration/60:.0f} min "
        f"(max safe: {max_duration_minutes:.0f} min) - {reason}"
    )

    return chunk_duration, reason


def _raise_if_load_oom(err: Exception) -> None:
    """Re-raise a GPU allocation failure at model load as ModelLoadError."""
    if 'out of memory' in str(err).lower():
        # Worded without the OOM terms so string classifiers do not read it as permanent.
        raise ModelLoadError(
            'Whisper model could not be loaded on the GPU at any precision') from err


class WhisperModelSingleton:
    _instance = None
    _base_model = None
    _current_model_name = None
    _needs_reload = False

    @classmethod
    def get_configured_model(cls) -> str:
        """Get the configured model: the failover override while set (#806),
        else the database setting."""
        override = _local_model_override.get()
        if override:
            return override
        try:
            from database import Database
            db = Database()
            model = db.get_setting('whisper_model')
            if model:
                return model
        except Exception as e:
            logger.warning(f"Could not read whisper_model from database: {e}")
        # Fall back to env var or default
        return os.getenv("WHISPER_MODEL", "small")

    @classmethod
    def mark_for_reload(cls):
        """Mark the model for reload on next use."""
        cls._needs_reload = True
        logger.info("Whisper model marked for reload")

    @classmethod
    def _should_reload(cls) -> str | None:
        """Check if model needs to be reloaded.

        Returns the configured model name if a reload is needed, None otherwise.
        This avoids a duplicate DB query in get_instance().
        """
        configured = cls.get_configured_model()
        if cls._needs_reload:
            return configured
        if cls._current_model_name and cls._current_model_name != configured:
            logger.info(f"Model changed from {cls._current_model_name} to {configured}")
            return configured
        return None

    @classmethod
    def unload_model(cls):
        """Unload the current model and free GPU memory.

        Call this after transcription is complete to free ~5-6GB memory
        before memory-intensive operations like speaker diarization.
        The model will lazy-reload on the next transcription request.
        """
        if cls._instance is not None or cls._base_model is not None:
            logger.info(f"Unloading Whisper model: {cls._current_model_name}")
            cls._instance = None
            cls._base_model = None
            cls._current_model_name = None
            cls._needs_reload = False

            # Force garbage collection and clear CUDA cache
            clear_gpu_memory()
            logger.info("CUDA cache cleared")

    @classmethod
    def is_loaded(cls) -> bool:
        return cls._instance is not None or cls._base_model is not None

    @classmethod
    def get_instance(cls) -> tuple[WhisperModel, BatchedInferencePipeline]:
        """
        Get both the base model and batched pipeline instance.
        Will reload if the configured model has changed.
        Returns:
            Tuple[WhisperModel, BatchedInferencePipeline]: Base model for operations like language detection,
                                                          and batched pipeline for transcription
        """
        # Check if we need to reload
        reload_model = cls._should_reload() if cls._instance is not None else None
        if reload_model:
            cls.unload_model()

        if cls._instance is None:
            _require_local_transcription()
            model_size = reload_model or cls.get_configured_model()
            device = resolve_whisper_device()
            configured_compute_type = _get_whisper_compute_type()

            # Resolve device and the compute type 'auto' falls back to.
            if device == "cuda":
                cuda_device_count = ctranslate2.get_cuda_device_count()
                if cuda_device_count > 0:
                    logger.info(f"CUDA available: {cuda_device_count} device(s) detected")
                    auto_compute_type = "float16"
                else:
                    logger.warning("CUDA requested but not available, falling back to CPU")
                    device = "cpu"
                    auto_compute_type = "int8"
            else:
                auto_compute_type = "int8"

            compute_type = (
                auto_compute_type if configured_compute_type == "auto"
                else configured_compute_type
            )
            logger.info(f"Initializing Whisper model: {model_size} on {device} with {compute_type}")

            # CTranslate2 requires compute capability >= 7.0 for float16. Pascal
            # consumer (CC 6.1), Maxwell (CC 5.x), and Jetson TX2 (CC 6.2) raise
            # at init. Walk the fallback chain only when float16 was selected;
            # any other explicit choice that fails is a config error, not a
            # hardware mismatch, so re-raise.
            try:
                cls._base_model = WhisperModel(
                    model_size, device=device, compute_type=compute_type,
                )
            except Exception as init_err:
                if device == "cuda" and compute_type == "float16":
                    last_err = init_err
                    for fallback in WHISPER_COMPUTE_TYPE_FALLBACK_CHAIN:
                        logger.warning(
                            f"Whisper float16 init failed on CUDA: {last_err}; "
                            f"retrying with {fallback}"
                        )
                        # Reclaim VRAM from any partial allocation before retry.
                        clear_gpu_memory()
                        try:
                            cls._base_model = WhisperModel(
                                model_size, device=device, compute_type=fallback,
                            )
                            compute_type = fallback
                            break
                        except Exception as retry_err:
                            last_err = retry_err
                    else:
                        _raise_if_load_oom(last_err)
                        # Preserve the original float16 failure as __cause__ so
                        # operators can see the root cause, not just the last
                        # fallback attempt's error.
                        raise last_err from init_err
                else:
                    if device == "cuda":
                        _raise_if_load_oom(init_err)
                    raise

            # Initialize batched pipeline
            cls._instance = BatchedInferencePipeline(
                cls._base_model
            )
            cls._current_model_name = model_size
            cls._needs_reload = False
            logger.info(
                f"Whisper model '{model_size}' and batched pipeline initialized "
                f"(device={device}, compute_type={compute_type})"
            )

            # Log actual GPU memory usage after model load
            mem_info = get_gpu_memory_info()
            if mem_info:
                allocated_gb = mem_info.get('allocated', 0) / (1024 ** 3)
                reserved_gb = mem_info.get('cached', 0) / (1024 ** 3)
                logger.info(f"GPU memory after model load: {allocated_gb:.2f}GB allocated, {reserved_gb:.2f}GB reserved")

        return cls._base_model, cls._instance

    @classmethod
    def get_batched_pipeline(cls) -> BatchedInferencePipeline:
        """
        Get just the batched pipeline for transcription
        Returns:
            BatchedInferencePipeline: Batched pipeline for efficient transcription
        """
        if cls._instance is None or cls._should_reload():
            cls.get_instance()
        return cls._instance

    @classmethod
    def get_current_model_name(cls) -> str | None:
        """Get the name of the currently loaded model."""
        return cls._current_model_name


def _whisper_api_rejects_word_timestamps(response) -> bool:
    """True when the server failed because word-level timestamps are unsupported.

    OpenAI proper returns the rejection as HTTP 400; OpenVINO Model Server
    surfaces the same condition as a 5xx with a MediaPipe error string. Detect
    by body-text marker so both shapes route into the segment-only fallback.
    """
    if response is None or response.status_code == 200:
        return False
    try:
        body = (response.text or '').lower()
    except Exception:
        return False
    return (
        'word timestamp' in body
        or 'timestamps not supported' in body
        or 'timestamp_granularities' in body
    )


def _whisper_api_rejects_vad_filter(response) -> bool:
    """True when the server 400s an unrecognized `vad_filter` form field.

    Narrow to that field name so only this specific rejection retries; any
    other non-200 falls through unchanged.
    """
    if response is None or response.status_code == 200:
        return False
    try:
        body = (response.text or '').lower()
    except Exception:
        return False
    return 'vad_filter' in body


def _effective_language(language_override: str | None, whisper_settings: dict[str, str]) -> str:
    """Resolve the effective Whisper language as a lowercased code.

    A non-empty per-call override beats the global whisper_language setting;
    blank falls through to the setting; default 'en'. 'auto' is preserved so
    callers can branch on it.
    """
    override = (language_override or '').strip().lower()
    if override:
        return override
    return (whisper_settings.get('language') or 'en').strip().lower()


_NOVAD_LABELS = {'novad_tail': 'Tail', 'novad_hole': 'Hole'}
# Within this many seconds a gap matches a recorded hole.
_HOLE_MATCH_S = 0.05


def _hole_recorded(hole, records) -> bool:
    """Whether a hole matches a recorded {start, end} within _HOLE_MATCH_S."""
    return any(abs(hole[0] - (rec.get('start') or 0.0)) <= _HOLE_MATCH_S
               and abs(hole[1] - (rec.get('end') or 0.0)) <= _HOLE_MATCH_S
               for rec in records if isinstance(rec, dict))


def _full_span_clips(duration: float | None) -> list[dict] | None:
    """Cover the whole file with 30s clips for a VAD-off transcription.

    BatchedInferencePipeline derives its chunks from VAD speech timestamps, so
    with VAD off it needs clip_timestamps or it raises. Returns None when the
    duration is unknown or too short to clip.
    """
    if not duration or duration <= 0:
        return None
    clips = [
        {'start': i * WHISPER_CHUNK_SECONDS,
         'end': min((i + 1) * WHISPER_CHUNK_SECONDS, duration)}
        for i in range(math.ceil(duration / WHISPER_CHUNK_SECONDS))
    ]
    # A trailing clip under MIN_CLIP_SECONDS is dropped: too short for a word,
    # and a boundary-straddling sliver reaches the pipeline as an empty
    # feature array and raises.
    return [c for c in clips
            if c['end'] - c['start'] >= MIN_CLIP_SECONDS] or None


class Transcriber:
    def __init__(self):
        # Model is now managed by singleton
        # Last local transcribe() outcome (batch_size/retry_count/device/etc),
        # read by callers that want it beside the per-phase stats (#519).
        self.last_transcription_stats = None
        if (_LOCAL_IMPORT_ERROR is not None
                and _get_whisper_settings()['backend'] != WHISPER_BACKEND_API):
            logger.warning(_local_unavailable_message())

    def _transcribe_via_api(
        self,
        audio_path: str,
        whisper_settings: dict[str, str] = None,
        language_override: str | None = None,
        preprocessed: bool = False,
        vad_filter: bool = True,
    ) -> list[dict] | None:
        """Transcribe audio using an OpenAI-compatible whisper API.

        Sends the preprocessed audio to a remote API endpoint and maps
        the verbose_json response to the internal segment format.

        Args:
            audio_path: Path to the audio file to transcribe.
            whisper_settings: Pre-fetched settings dict from _get_whisper_settings().
            preprocessed: The file already went through the preprocess filter
                chain (extract_audio_chunk(preprocess=True)); skip the
                redundant preprocess pass.
            vad_filter: False sends `vad_filter=false` so a server that
                supports the switch keeps quiet audio (tail pass, spec 1.2).
                True sends nothing, leaving the server's own default alone.

        Returns:
            List of transcript segments, or None on failure.
        """
        preprocessed_path = None
        flac_path = None
        try:
            if whisper_settings is None:
                whisper_settings = _get_whisper_settings()
            base_url = whisper_settings['api_base_url']
            api_key = whisper_settings['api_key']
            model = whisper_settings['api_model']

            if not base_url:
                logger.error("Whisper API base URL not configured")
                return None

            # Preprocess audio for consistent quality (skipped when the
            # chunk was extracted with the filter chain already applied)
            if not preprocessed:
                preprocessed_path = self.preprocess_audio(audio_path)
            transcribe_path = preprocessed_path if preprocessed_path else audio_path

            # After preprocessing, compress to FLAC for upload (lossless, ~4-5x smaller than WAV).
            # Prevents 413 errors from APIs with tight upload limits (e.g. OpenRouter).
            # Self-hosted Whisper servers that accept WAV directly can opt out via the
            # skip_flac_compression setting and avoid the extra encode pass.
            skip_flac = bool(whisper_settings.get('skip_flac_compression', False))
            if not skip_flac and transcribe_path.endswith('.wav'):
                fd, flac_path = tempfile.mkstemp(suffix='.flac')
                os.close(fd)
                try:
                    ffmpeg_result = tracked_run(
                        ['ffmpeg', *SAFE_MEDIA_INPUT_ARGS, '-y', '-i', transcribe_path, '-c:a', 'flac', flac_path],
                        capture_output=True, timeout=FFMPEG_SHORT_TIMEOUT,
                    )
                    if ffmpeg_result.returncode == 0 and os.path.exists(flac_path):
                        transcribe_path = flac_path
                        logger.info(f"Compressed for upload: {os.path.getsize(flac_path) / 1024 / 1024:.1f}MB FLAC")
                    else:
                        # Compression failed -- remove orphaned temp file
                        _unlink_quiet(flac_path)
                        flac_path = None
                except Exception as e:
                    logger.warning(f"FLAC compression failed, sending WAV: {e}")
                    _unlink_quiet(flac_path)
                    flac_path = None

            # Build request
            url = _transcription_url(base_url)

            headers = _bearer_headers(api_key)

            form_data_base = {
                'model': model,
                'response_format': 'verbose_json',
            }
            # Only sent when disabling VAD: a server without the switch
            # ignores an unknown field, but never send a redundant default.
            if not vad_filter:
                form_data_base['vad_filter'] = 'false'
            language = _effective_language(language_override, whisper_settings)
            if language and language != 'auto':
                form_data_base['language'] = language

            # Defensive: refuse to upload tiny or missing files. Avoids
            # remote "empty audio" / decode failures when preprocessing
            # silently produced an unusable chunk.
            try:
                upload_size = os.path.getsize(transcribe_path)
            except OSError:
                upload_size = 0
            if upload_size < 1024:
                logger.error(
                    f"Refusing to upload suspiciously small audio to Whisper API: "
                    f"{upload_size} bytes at {transcribe_path}"
                )
                return None

            logger.info("Sending audio to whisper API (size=%.1fMB)", upload_size / 1024 / 1024)

            # Whisper API URLs are operator-typed, so OPERATOR_CONFIGURED
            # trust (private / loopback allowed for self-hosted whisper.cpp
            # servers; cloud metadata and downgrades refused per-hop).
            # safe_post does not retry; wrap in a small backoff loop so
            # transient upstream blips do not fail a full transcription.
            # Some OpenAI-compatible servers (OpenVINO Model Server, older
            # faster-whisper-server builds) reject ['segment','word'] outright;
            # outer loop retries once with segment-only granularity if the
            # response body signals that rejection.
            response = None
            last_request_exc = None
            max_attempts = int(whisper_settings.get('max_attempts') or 2)
            granularity_modes = (
                ['segment', 'word'],
                ['segment'],
            )
            # Some servers 400 an unrecognized vad_filter field instead of
            # ignoring it; drop it and retry once rather than lose the upload.
            vad_filter_retried = False
            while True:
                for gran_idx, granularities in enumerate(granularity_modes):
                    form_data = {
                        **form_data_base,
                        'timestamp_granularities[]': granularities,
                    }
                    # Wall-clock deadline for 429 retries (which do not consume an
                    # attempt slot, see below) so a Retry-After: 0 loop cannot spin.
                    # Set on the first permit below, per granularity mode.
                    retry_deadline = None
                    attempt = 0
                    while attempt < max_attempts:
                        try:
                            with open(transcribe_path, 'rb') as audio_file:
                                permit_wait_start = time.monotonic()
                                with get_pool().slot():
                                    # Waiting on our own admission control is not
                                    # the provider throttling us, so it never
                                    # eats the 429 window.
                                    now = time.monotonic()
                                    if retry_deadline is None:
                                        retry_deadline = now + _api_timeout(whisper_settings)
                                    else:
                                        retry_deadline += now - permit_wait_start
                                    response = safe_post(
                                        url,
                                        trust=URLTrust.OPERATOR_CONFIGURED,
                                        timeout=_api_timeout(whisper_settings),
                                        max_redirects=HTTP_MAX_REDIRECTS_API,
                                        files={'file': (os.path.basename(transcribe_path), audio_file)},
                                        data=form_data,
                                        headers=headers,
                                    )
                        except SSRFError as exc:
                            logger.warning(f"Whisper API URL blocked: {exc}")
                            return None
                        except requests.RequestException as exc:
                            logger.warning(
                                "Whisper API attempt %d/%d failed: %s",
                                attempt + 1, max_attempts, exc,
                            )
                            last_request_exc = exc
                            response = None
                            attempt += 1
                            continue
                        if response.status_code == 429 and get_pool().active:
                            if time.monotonic() >= retry_deadline:
                                logger.warning(
                                    "%sWhisper API still busy (429) after the "
                                    "retry deadline; giving up", _log_prefix())
                                return None
                            # Floor so a Retry-After: 0 (or absent) header still yields.
                            parsed_retry_after = parse_retry_after(
                                (response.headers or {}).get('Retry-After'), max_seconds=300.0)
                            retry_after = max(
                                parsed_retry_after if parsed_retry_after is not None else 5.0, 0.5)
                            logger.warning(
                                "%sWhisper API busy (429); waiting %.1fs before retrying",
                                _log_prefix(), retry_after)
                            time.sleep(retry_after)
                            response = None
                            continue
                        if response.status_code < 500:
                            break
                        logger.warning(
                            "Whisper API attempt %d/%d returned %d",
                            attempt + 1, max_attempts, response.status_code,
                        )
                        attempt += 1

                    if response is None:
                        # Every attempt raised at the transport layer, so the
                        # endpoint is unreachable. The offline queue (#482) must
                        # tell that apart from a bad response.
                        raise ServiceUnavailableError(
                            'whisper', f"Whisper API unreachable: {last_request_exc}")
                    if response.status_code == 200:
                        break
                    if gran_idx == 0 and _whisper_api_rejects_word_timestamps(response):
                        logger.warning(
                            "Whisper API does not support word timestamps; "
                            "retrying with segment-only timestamps"
                        )
                        continue
                    # Non-200 with no word-timestamp signal, so give up here.
                    break

                if (response.status_code != 200 and not vad_filter_retried
                        and 'vad_filter' in form_data_base
                        and _whisper_api_rejects_vad_filter(response)):
                    logger.warning(
                        "Whisper API rejected vad_filter field; retrying without it"
                    )
                    form_data_base.pop('vad_filter', None)
                    vad_filter_retried = True
                    continue
                break

            if response is None or response.status_code != 200:
                if response is not None and response.status_code in (401, 402, 403, 404):
                    raise TranscriptionRejectedError(response.status_code)
                if response is not None and response.status_code >= 500:
                    raise ServiceUnavailableError(
                        'whisper',
                        f"Whisper API returned {response.status_code} after retries")
                return None

            # Parse verbose_json response
            resp_json = response.json()
            segments = resp_json.get('segments', [])
            result = []

            for seg in segments:
                words = []
                for w in seg.get('words', []):
                    words.append({
                        'word': w.get('word', ''),
                        'start': w.get('start', 0),
                        'end': w.get('end', 0),
                    })

                text = seg.get('text', '').strip()
                if not text:
                    continue

                result.append({
                    'start': seg.get('start', 0),
                    'end': seg.get('end', 0),
                    'text': text,
                    'words': words,
                })

            # Spec-compliant servers return words in a top-level array, not
            # nested per segment; fold them in so boundary refinement sees them.
            _attach_top_level_words(result, resp_json.get('words') or [])

            # response is a 200 here; non-200/None returned above
            if not result:
                body_len = len(response.text) if response.text else 0
                logger.warning(
                    "Whisper API returned 200 but 0 usable segments (body %d chars). "
                    "This often means the server failed to decode the audio "
                    "(e.g. --convert writing to a non-writable directory).",
                    body_len,
                )

            # Filter hallucinations
            original_count = len(result)
            result = self.filter_hallucinations(result)
            if len(result) < original_count:
                logger.info(f"Filtered {original_count - len(result)} hallucination segments")

            duration_min = result[-1]['end'] / 60 if result else 0
            logger.info(f"API transcription completed: {len(result)} segments, {duration_min:.1f} minutes")

            return result

        except (ServiceUnavailableError, TranscriptionRejectedError):
            raise
        except Exception as e:
            logger.error(f"API transcription failed: {e}")
            return None
        finally:
            _unlink_quiet(preprocessed_path)
            _unlink_quiet(flac_path)

    def _record_local_stats(self, outcome, batch_size, retry_count, device, model,
                            error=None) -> None:
        """Store and persist the outcome of a local batched transcription."""
        stats = {
            'outcome': outcome,
            'batch_size': batch_size,
            'retry_count': retry_count,
            'retry_succeeded': outcome == 'success' and retry_count > 0,
            'device': device,
            'gpu_device_name': get_gpu_device_name() if device == 'cuda' else None,
            'model': model,
        }
        if error is not None:
            stats['error'] = error[:500]
        self.last_transcription_stats = stats
        _record_local_transcription_outcome(stats)

    def _transcribe_sequential(self, audio_path: str,
                               language_override: str | None = None) -> list[dict] | None:
        """No-VAD decode on the base WhisperModel with its temperature fallback; None on failure.

        The batched pipeline can skip the start of a clip; this path is for short repair spans.
        """
        whisper_settings = active_whisper_settings()
        if whisper_settings['backend'] == WHISPER_BACKEND_API:
            return self._transcribe_via_api(
                audio_path, whisper_settings, language_override=language_override,
                vad_filter=False)
        _require_local_transcription()
        language_setting = _effective_language(language_override, whisper_settings)
        language = None if language_setting == 'auto' else (language_setting or 'en')
        preprocessed_path = None
        acquired = _gpu_admission_acquire(resolve_whisper_device())
        try:
            preprocessed_path = self.preprocess_audio(audio_path)
            model, _batched = WhisperModelSingleton.get_instance()
            segments, _info = model.transcribe(
                preprocessed_path or audio_path, language=language, beam_size=5,
                word_timestamps=True, vad_filter=False)
            return [{
                'start': seg.start,
                'end': seg.end,
                'text': seg.text.strip(),
                'words': [{'word': w.word, 'start': w.start, 'end': w.end}
                          for w in seg.words or []],
            } for seg in segments]
        except ModelLoadError:
            raise
        except Exception as e:
            logger.error(f"{_log_prefix()}Sequential transcription failed: {e}")
            return None
        finally:
            if acquired:
                _gpu_admission_release()
            _unlink_quiet(preprocessed_path)

    def transcribe_span_no_vad(self, audio_path: str, start: float, end: float,
                               language_override: str | None, flag: str,
                               quiet_db: float | None = None) -> tuple[list[dict] | None, str | None]:
        """Sequential no-VAD decode of start-end. Returns (offset segments flagged `flag` or None, empty_reason).

        empty_reason is 'quiet', 'unreadable' or 'no_speech' when the span should not be retried.
        With quiet_db set, a span below it (or with an unreadable volume) is skipped before extraction.
        ModelLoadError propagates; other failures return (None, None).
        """
        label = _NOVAD_LABELS[flag]
        prefix = _log_prefix()
        if quiet_db is not None:
            volume = mean_volume_db(audio_path, start, end - start)
            if volume is None or volume < quiet_db:
                reading = 'unreadable' if volume is None else f'{volume:.1f} dB'
                logger.info(f"{prefix}{label} {start:.1f}s-{end:.1f}s mean volume {reading}; skipping")
                return None, 'unreadable' if volume is None else 'quiet'
        try:
            chunk_path = extract_audio_chunk(audio_path, start, end)
        except AudioExtractionTimeout as e:
            logger.warning(f"{prefix}{label} chunk extraction timed out; skipping: {e}")
            return None, None
        if not chunk_path:
            logger.warning(f"{prefix}{label} chunk extraction failed; skipping")
            return None, None
        try:
            new_segments = self._transcribe_sequential(chunk_path, language_override)
        except ModelLoadError:
            raise
        except Exception as e:
            logger.warning(f"{prefix}{label} re-transcription failed; "
                           f"proceeding without {label.lower()}: {e}")
            return None, None
        finally:
            _unlink_quiet(chunk_path)
        if new_segments is None:
            logger.warning(f"{prefix}{label} re-transcription failed; "
                           f"proceeding without {label.lower()}")
            return None, None
        new_segments = self.filter_hallucinations(new_segments)
        if not new_segments:
            logger.info(f"{prefix}{label} re-transcription produced no segments")
            return None, 'no_speech'
        for seg in new_segments:
            seg['start'] += start
            seg['end'] += start
            for word in seg.get('words') or []:
                word['start'] += start
                word['end'] += start
            seg[flag] = True
        logger.info(
            f"{prefix}{label} re-transcription added {len(new_segments)} segment(s) "
            f"({new_segments[0]['start']:.1f}s-{new_segments[-1]['end']:.1f}s)")
        return new_segments, None

    @staticmethod
    def unload_after_repair() -> None:
        """Free the model the repair decodes reloaded after transcribe_chunked unloaded it."""
        with _all_admission_permits() as free:
            if not free:
                logger.debug("Skipping the post-repair Whisper unload: a local transcription holds the GPU")
                return
            if WhisperModelSingleton.is_loaded():
                WhisperModelSingleton.unload_model()
                logger.info("Whisper model unloaded after transcript repair")

    def repair_gaps(self, audio_path: str, segments: list[dict], min_s: float,
                    language_override: str | None = None,
                    skip=()) -> tuple[list[dict], list[dict]]:
        """Re-decode transcript gaps of min_s or more the batched decoder skipped.

        Holes matching a `skip` record are left alone; caps keep the largest holes.
        Returns (added segments flagged novad_hole, holes that held nothing as {start, end, reason}).
        """
        prefix = _log_prefix()
        holes = transcript_gaps(segments, min_s)
        known = [hole for hole in holes if _hole_recorded(hole, skip)]
        if known:
            logger.info(f"{prefix}Skipping {len(known)} untranscribed hole(s) "
                        f"already re-transcribed without speech")
            holes = [hole for hole in holes if hole not in known]
        picked, skipped = [], []
        budget = HOLE_RETRANSCRIBE_MAX_SECONDS
        for hole in sorted(holes, key=lambda h: h[1] - h[0], reverse=True):
            length = hole[1] - hole[0]
            if len(picked) < HOLE_RETRANSCRIBE_MAX_HOLES and length <= budget:
                picked.append(hole)
                budget -= length
            else:
                skipped.append(hole)
        if skipped:
            logger.info(
                f"{prefix}Skipping {len(skipped)} untranscribed hole(s) "
                f"({sum(e - s for s, e in skipped):.1f} s) over the per-episode cap of "
                f"{HOLE_RETRANSCRIBE_MAX_HOLES} holes / {HOLE_RETRANSCRIBE_MAX_SECONDS:.0f} s")
        added, empty = [], []
        for hole_start, hole_end in sorted(picked):
            logger.info(
                f"{prefix}Untranscribed hole {hole_start:.1f}s-{hole_end:.1f}s "
                f"({hole_end - hole_start:.1f} s); re-transcribing without VAD")
            recovered, reason = self.transcribe_span_no_vad(
                audio_path, hole_start, hole_end, language_override, 'novad_hole',
                quiet_db=HOLE_RETRANSCRIBE_QUIET_DB)
            if recovered:
                added.extend(recovered)
            elif reason:
                empty.append({'start': hole_start, 'end': hole_end, 'reason': reason})
        return added, empty

    def filter_hallucinations(self, segments: list[dict]) -> list[dict]:
        """Filter out common Whisper hallucinations and artifacts."""
        filtered = []
        for seg in segments:
            text = seg.get('text', '').strip()
            if not text:
                continue
            if HALLUCINATION_PATTERNS.match(text.rstrip(TRAILING_PUNCTUATION)):
                logger.debug("Filtered hallucination segment (%d chars)", len(text))
                continue
            # Skip repeated segments (Whisper loop artifacts)
            if filtered and text == filtered[-1].get('text', '').strip():
                logger.debug("Filtered repeated segment (%d chars)", len(text))
                continue
            filtered.append(seg)
        return filtered

    @staticmethod
    def _should_detect_foreign_language(transcribe_language, detected_lang) -> bool:
        """Run the foreign-language DAI detector only when the audio is English.

        The detector flags non-English ad insertions inside English podcasts.
        On a non-English podcast its heuristics match every segment, so we
        skip it unless the user pinned ``whisper_language='en'`` or chose
        ``'auto'`` and Whisper detected an English variant.
        """
        if transcribe_language == 'en':
            return True
        if transcribe_language is None:
            return (detected_lang or '').lower().startswith('en')
        return False

    def _detect_non_english_segment(self, text: str, primary_language: str) -> bool:
        """Detect if a segment is likely non-English (potential DAI ad).

        Uses multiple heuristics:
        1. High ratio of non-ASCII characters (Spanish, etc.)
        2. Common Spanish/other language patterns
        3. If primary detected language is not English and segment has markers

        Args:
            text: The segment text
            primary_language: The overall detected language from Whisper

        Returns:
            True if segment appears to be non-English
        """
        if not text or len(text) < 10:
            return False

        # Check for high ratio of accented/non-ASCII characters
        non_ascii_chars = sum(1 for c in text if ord(c) > 127)
        non_ascii_ratio = non_ascii_chars / len(text)

        # Spanish and other language indicators
        spanish_patterns = [
            'usted', 'puede', 'para', 'como', 'ahora', 'llame', 'gratis',
            'oferta', 'hoy', 'desde', 'hasta', 'numero', 'telefono',
            'visite', 'compre', 'ahorre', 'descuento', 'promocion'
        ]

        text_lower = text.lower()

        # Check for Spanish ad patterns
        spanish_word_count = sum(1 for word in spanish_patterns if word in text_lower)

        # Heuristics for non-English detection
        is_likely_foreign = (
            # High non-ASCII ratio suggests accented language
            non_ascii_ratio > 0.05 or
            # Multiple Spanish words detected
            spanish_word_count >= 2 or
            # Primary language is not English and segment has some markers
            (primary_language not in ['en', 'english', 'unknown'] and
             (non_ascii_ratio > 0.02 or spanish_word_count >= 1))
        )

        if is_likely_foreign:
            logger.debug(f"Non-English segment detected: non_ascii={non_ascii_ratio:.2f}, "
                        f"spanish_words={spanish_word_count}, primary_lang={primary_language}")

        return is_likely_foreign

    def get_audio_duration(self, audio_path: str) -> float | None:
        """Get audio duration in seconds using ffprobe.

        Delegates to utils.audio.get_audio_duration for consistent implementation.
        """
        duration = get_audio_duration(audio_path)
        if duration is not None:
            logger.info(f"Audio duration: {duration:.1f}s ({duration/60:.1f} min)")
        return duration

    # Largest batch size proven to fit this device's VRAM: recorded when a run
    # completes below the duration tier, so later episodes start at a size that
    # fits. After the TTL the next run probes one size up, so a transient OOM
    # cannot pin every later episode for good.
    BATCH_CEILING_SETTING = 'transcribe_batch_size_ceiling'
    BATCH_CEILING_TTL_DAYS = 2

    @staticmethod
    def _batch_ceiling_device() -> str:
        """Device name the stored ceiling applies to; VRAM differs per GPU model."""
        return get_gpu_device_name()

    def _read_ceiling(self) -> dict | None:
        """Stored ceiling for this device as {'size', 'recorded_at'}, or None
        when unset, malformed, or recorded for a different device. recorded_at
        is None for pre-2.96.0 payloads, which read as expired."""
        # Inline import: see _get_whisper_settings above, Database would be a
        # circular import at module level.
        from database import Database
        try:
            raw = Database().get_setting(self.BATCH_CEILING_SETTING)
        except Exception as e:
            logger.debug(f"Could not read batch size ceiling: {e}")
            return None
        if not raw:
            return None
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            parsed = None
        if not isinstance(parsed, dict) or parsed.get('device') != self._batch_ceiling_device():
            return None
        try:
            size = max(1, int(parsed['size']))
        except (KeyError, TypeError, ValueError):
            return None
        return {'size': size, 'recorded_at': parsed.get('recorded_at')}

    def _ceiling_expired(self, entry: dict) -> bool:
        recorded_at = parse_iso_utc(entry['recorded_at'])
        return (recorded_at is None
                or utc_now() - recorded_at > timedelta(days=self.BATCH_CEILING_TTL_DAYS))

    def _batch_size_ceiling(self) -> int | None:
        """Unexpired stored ceiling as a positive int, else None."""
        entry = self._read_ceiling()
        if entry is None or self._ceiling_expired(entry):
            return None
        return entry['size']

    def record_batch_size_ceiling(self, batch_size: int) -> None:
        """Persist batch_size as this device's ceiling. Called only after a
        completed run, since only completion proves a size fits; ratchets down
        against an unexpired ceiling and keeps its timestamp so it still ages out.
        """
        from database import Database
        candidate = max(1, int(batch_size))
        entry = self._read_ceiling()
        live = entry if entry and not self._ceiling_expired(entry) else None
        value = min(live['size'], candidate) if live else candidate
        recorded_at = live['recorded_at'] if live and value == live['size'] else utc_now_iso()
        payload = json.dumps({
            'device': self._batch_ceiling_device(), 'size': value, 'recorded_at': recorded_at,
        })
        try:
            Database().set_setting(self.BATCH_CEILING_SETTING, payload)
        except Exception as e:
            logger.debug(f"Could not persist batch size ceiling: {e}")

    @staticmethod
    def _tier_batch_size(duration_seconds: float | None) -> int:
        """Batch size from audio duration alone; longer episodes need smaller batches."""
        if duration_seconds is None:
            return 8
        for threshold, batch_size in BATCH_SIZE_TIERS:
            if duration_seconds < threshold:
                return batch_size
        return 4

    def get_batch_size_for_duration(self, duration_seconds: float | None) -> int:
        """Duration tier, clamped by an unexpired ceiling; an expired one is
        probed one size up."""
        tier = self._tier_batch_size(duration_seconds)
        entry = self._read_ceiling()
        if entry is None:
            return tier
        if self._ceiling_expired(entry):
            return min(tier, entry['size'] * 2)
        return min(tier, entry['size'])

    def clear_cuda_cache(self):
        """Clear CUDA cache to free GPU memory.

        Delegates to utils.gpu.clear_gpu_memory().
        """
        clear_gpu_memory()
        logger.info("CUDA cache cleared")

    def preprocess_audio(self, input_path: str) -> str | None:
        """
        Normalize audio for consistent transcription.
        Returns path to preprocessed file, or None if preprocessing fails.
        Caller is responsible for cleaning up the returned temp file.
        """
        fd, output_path = tempfile.mkstemp(suffix='.wav')
        os.close(fd)
        success = False

        try:
            cmd = [
                'ffmpeg', *SAFE_MEDIA_INPUT_ARGS, '-y', '-i', input_path,
                '-vn',           # ignore embedded cover art (#556)
                '-ar', '16000',  # 16kHz (Whisper native sample rate)
                '-ac', '1',      # Mono
                '-af', PREPROCESS_AUDIO_FILTERS,
                output_path
            ]
            # Scale timeout by file size: 10s per MB (~1MB/min for podcasts)
            # e.g. 50MB file = 500s, 100MB = 1000s, floor at FFMPEG_LONG_TIMEOUT (300s)
            file_size_mb = os.path.getsize(input_path) / (1024 * 1024)
            preprocess_timeout = max(FFMPEG_LONG_TIMEOUT, int(file_size_mb * 10))
            result = tracked_run(cmd, capture_output=True, timeout=preprocess_timeout)
            if result.returncode == 0:
                logger.info(f"Audio preprocessed: {input_path} -> {output_path}")
                success = True
                return output_path
            logger.warning(
                f"Audio preprocessing failed (returncode={result.returncode}), "
                f"stderr: {_ffmpeg_error_tail(result.stderr)}"
            )
            return None
        except subprocess.TimeoutExpired:
            logger.warning(f"Audio preprocessing timed out after {preprocess_timeout}s, using original")
            return None
        except Exception as e:
            logger.warning(f"Audio preprocessing error: {e}, using original")
            return None
        finally:
            # Clean up temp file on any failure path
            if not success:
                _unlink_quiet(output_path)

    def check_audio_availability(self, url: str, timeout: int = 10,
                                 user_agent: str = None) -> tuple:
        """Check if audio URL is accessible without downloading.

        Performs a HEAD request to verify the CDN has the file ready.
        Use this before downloading to avoid failures on newly published episodes
        where the CDN hasn't propagated the file yet.

        Args:
            url: Audio file URL to check
            timeout: Request timeout in seconds
            user_agent: Sent instead of the download User-Agent when given

        Returns:
            Tuple of (available: bool, error_message: str or None)
        """
        from utils.safe_http import safe_head
        headers = {'User-Agent': user_agent or download_user_agent()}
        try:
            response = safe_head(
                url,
                trust=URLTrust.FEED_CONTENT,
                timeout=timeout,
                # Megaphone / Art19 / simplecast often chain 6-8 redirects
                # (CDN edge -> regional -> asset), and Acast adds analytics
                # bouncers on top. Bumped to 10 so we don't false-fail
                # CDN checks for legitimate feeds.
                max_redirects=HTTP_MAX_REDIRECTS_FEED,
                headers=headers,
            )
        except SSRFError as e:
            logger.warning(f"SSRF blocked in check_audio_availability: {e}")
            return False, f"URL blocked: {e}"
        except requests.exceptions.Timeout:
            return False, "CDN timeout"
        except requests.RequestException as e:
            return False, f"CDN check failed: {e}"

        keep_query = log_download_query_enabled()
        logger.info("Checking audio availability: "
                    f"{safe_url_for_log(url, keep_path=True, keep_query=keep_query)}")
        for line in redirect_chain_for_log(response, keep_query=keep_query):
            logger.info(line)

        if response.status_code == 200:
            return True, None
        if response.status_code == 403:
            # Distinct from the 404 below so the retry classifier can treat it
            # as permanent: an access decision does not change on replay.
            return False, f"{CDN_REFUSED_PREFIX} the request (403)"
        if response.status_code == 404:
            return False, "CDN not ready (404)"
        if response.status_code >= 500:
            return False, f"CDN server error ({response.status_code})"
        return True, None

    def download_audio(self, url: str, timeout: tuple = (10, 300),
                       user_agent: str = None) -> str | None:
        """Download audio file from URL.

        Args:
            url: Audio file URL
            timeout: (connect_timeout, read_timeout) in seconds
            user_agent: Sent instead of the download User-Agent when given

        Raises:
            AudioTooLargeError: the enclosure exceeds the MAX_AUDIO_DOWNLOAD_MB
                cap (default 500MB). Other failures keep the return-None
                contract.
        """
        keep_query = log_download_query_enabled()
        try:
            logger.info("Downloading audio from: "
                        f"{safe_url_for_log(url, keep_path=True, keep_query=keep_query)}")
            headers = {
                'User-Agent': user_agent or download_user_agent(),
                'Accept': '*/*',
                'Accept-Language': 'en-US,en;q=0.9',
            }
            response = safe_get(
                url,
                trust=URLTrust.FEED_CONTENT,
                timeout=timeout,
                max_redirects=HTTP_MAX_REDIRECTS_FEED,
                stream=True,
                headers=headers,
            )
            for line in redirect_chain_for_log(response, keep_query=keep_query):
                logger.info(line)
            response.raise_for_status()
        except SSRFError as e:
            logger.warning(f"SSRF blocked in download_audio: {e}")
            return None

        temp_path = None
        try:
            max_mb = _max_download_mb()
            max_bytes = max_mb * 1024 * 1024

            # Check file size (isdigit guards malformed headers; the stream
            # cap below still protects when the header is unusable)
            content_length = response.headers.get('Content-Length')
            if content_length and content_length.isdigit():
                size_mb = int(content_length) / (1024 * 1024)
                if size_mb > max_mb:
                    response.close()
                    raise ResponseTooLargeError(
                        f"Audio file is {size_mb:.1f}MB, over the {max_mb}MB download cap"
                    )
                logger.info(f"Audio file size: {size_mb:.1f}MB")

            # Cap the stream independent of Content-Length (absent on chunked
            # responses) so a feed enclosure can't fill the disk (transcription-1).
            with tempfile.NamedTemporaryFile(delete=False, suffix='.mp3') as tmp:
                temp_path = tmp.name
                try:
                    stream_to_file_capped(response, tmp, max_bytes)
                except ResponseTooLargeError:
                    response.close()
                    raise ResponseTooLargeError(
                        f"Audio stream exceeded the {max_mb}MB download cap"
                    ) from None

            logger.info(f"Downloaded audio to: {temp_path}")
            return temp_path
        # Only the two audio-cap raises above can reach this handler:
        # safe_get runs in the first try block, and nothing else in this
        # try uses the capped-read helpers.
        except ResponseTooLargeError as e:
            _unlink_quiet(temp_path)
            logger.error(f"{e} ({safe_url_for_log(url)})")
            raise AudioTooLargeError(str(e)) from e
        except Exception as e:
            _unlink_quiet(temp_path)
            logger.error(f"Failed to download audio: {e}")
            return None

    def transcribe(
        self,
        audio_path: str,
        language_override: str | None = None,
        vad_filter: bool = True,
        preprocessed: bool = False,
        whisper_settings: dict[str, str] | None = None,
    ) -> list[dict]:
        """Transcribe audio file using Faster Whisper with batched pipeline.

        Uses adaptive batch sizing based on audio duration to prevent CUDA OOM errors.
        Automatically retries with smaller batch size on OOM.

        `language_override` (when non-empty) takes precedence over the global
        whisper_language setting for this call only -- used to honor per-feed
        language overrides without mutating shared settings.

        ``vad_filter=False`` disables Whisper's VAD (tail re-transcription,
        spec 1.2). The API backend forwards it as a `vad_filter=false` form
        field; servers without the switch ignore it.

        ``preprocessed=True`` means the caller already applied the preprocess
        filter chain (chunks from extract_audio_chunk(preprocess=True)), so
        the redundant preprocess pass is skipped.

        ``whisper_settings``, when given, is used as-is instead of resolving
        the active/failover config (#806); callers already holding a settings
        dict (e.g. a chunk loop) pass it through so every chunk agrees.
        """
        whisper_settings = whisper_settings or active_whisper_settings()
        _note_whisper_settings(whisper_settings)
        if whisper_settings['backend'] == WHISPER_BACKEND_API:
            return self._transcribe_via_api(
                audio_path, whisper_settings,
                language_override=language_override,
                preprocessed=preprocessed,
                vad_filter=vad_filter,
            )
        # Outside the try below so the actionable message is not swallowed.
        _require_local_transcription()

        # Local decode under the failover config pins the configured failover
        # model; reset in the finally below so later calls see the primary
        # model again.
        override_token = None
        failover_local_model = (
            whisper_settings.get('local_model') if whisper_settings.get('is_failover') else None)
        if failover_local_model:
            override_token = _local_model_override.set(failover_local_model)

        # try opens immediately after the override set above so the finally
        # resets it on every exit path, including a failure before any of
        # the lines below run.
        try:
            preprocessed_path = None
            # Defensive defaults: referenced in the stats recording below even if
            # an exception hits before the real assignments further down.
            device = None
            batch_size = None
            current_model = None
            retry_count = 0
            # Admission guard (module-level, see GPU_TRANSCRIBE_MAX_CONCURRENT):
            # taken for the whole call so preprocessing and every retry attempt
            # for this episode hold the device before another local transcription
            # can start. Released in the finally below on every exit path.
            # Bound before the call itself in case resolve_whisper_device() or
            # the acquire raises, so the finally below never sees it unbound.
            gpu_admission_acquired = False
            gpu_admission_acquired = _gpu_admission_acquire(resolve_whisper_device())

            language_setting = _effective_language(language_override, whisper_settings)
            transcribe_language = None if language_setting == 'auto' else (language_setting or 'en')

            # Get audio duration for adaptive batch sizing
            audio_duration = self.get_audio_duration(audio_path)

            # Get the batched pipeline for efficient transcription
            model = WhisperModelSingleton.get_batched_pipeline()
            current_model = WhisperModelSingleton.get_current_model_name()

            logger.info(f"Starting transcription of: {audio_path} (model: {current_model})")

            # Preprocess audio for consistent quality (skipped when the
            # chunk was extracted with the filter chain already applied)
            if not preprocessed:
                preprocessed_path = self.preprocess_audio(audio_path)
            transcribe_path = preprocessed_path if preprocessed_path else audio_path
            if preprocessed_path and not vad_filter:
                # The no-VAD clips must cover the file actually transcribed;
                # loudnorm can shift the duration by tens of milliseconds.
                audio_duration = (self.get_audio_duration(preprocessed_path)
                                  or audio_duration)

            # Adjust batch size based on device and audio duration
            device = resolve_whisper_device()
            if device == "cuda":
                # Use adaptive batch size based on duration to prevent OOM
                batch_size = self.get_batch_size_for_duration(audio_duration)
                duration_str = f"{audio_duration/60:.1f} min" if audio_duration else "unknown"
                logger.info(f"Using adaptive batch size: {batch_size} (duration: {duration_str})")
            else:
                batch_size = 8  # Smaller batch for CPU

            tier_batch_size = self._tier_batch_size(audio_duration)

            # Retry logic for CUDA OOM errors
            max_retries = 3
            retry_count = 0
            reload_model = False
            segments_generator = None

            while retry_count < max_retries:
                if reload_model:
                    segments_generator = None
                    model = None
                    WhisperModelSingleton.unload_model()
                    clear_gpu_memory()
                    model = WhisperModelSingleton.get_batched_pipeline()
                    reload_model = False
                try:
                    # Clear CUDA cache before each attempt
                    if device == "cuda":
                        self.clear_cuda_cache()

                    # word_timestamps=True enables precise boundary refinement later.
                    # Pinning a language prevents Whisper from misdetecting on music
                    # intros or sound effects (e.g. English podcast detected as Spanish
                    # at 93% confidence). `whisper_language=auto` (or blank env var)
                    # lets Whisper auto-detect -- useful for multilingual / non-English
                    # podcasts. The non-English DAI heuristic downstream
                    # (_detect_non_english_segment) is gated by
                    # _should_detect_foreign_language so it only runs when the audio
                    # is English; on non-English podcasts it would false-positive
                    # every segment.
                    segments_generator, info = model.transcribe(
                        transcribe_path,
                        language=transcribe_language,
                        beam_size=5,
                        batch_size=batch_size,
                        word_timestamps=True,  # Enable word-level timestamps for boundary refinement
                        vad_filter=vad_filter,
                        vad_parameters=dict(
                            min_silence_duration_ms=1000,  # Increased from 500 - less aggressive skipping
                            speech_pad_ms=600,  # Increased from 400 - more padding for ad segments
                            threshold=0.3  # Lower threshold = more sensitive to speech in ads
                        ) if vad_filter else None,
                        # None here when duration probing failed: a no-VAD
                        # span of 30s or more then still fails, and the caller
                        # logs that as a failure rather than silence.
                        clip_timestamps=(
                            None if vad_filter
                            else _full_span_clips(audio_duration)),
                    )

                    # Log detected language
                    detected_lang = info.language if hasattr(info, 'language') else 'unknown'
                    lang_prob = info.language_probability if hasattr(info, 'language_probability') else 0
                    logger.info(f"Detected primary language: {detected_lang} (probability: {lang_prob:.2f})")

                    should_detect_foreign = self._should_detect_foreign_language(
                        transcribe_language, detected_lang
                    )
                    logger.info(
                        "Foreign-language DAI detector: %s",
                        "enabled" if should_detect_foreign else "disabled",
                    )

                    # Collect segments with real-time progress logging
                    result = []
                    segment_count = 0
                    last_log_time = 0

                    non_english_count = 0
                    for segment in segments_generator:
                        segment_count += 1
                        # Store word-level timestamps for boundary refinement
                        words = []
                        if segment.words:
                            for w in segment.words:
                                words.append({
                                    "word": w.word,
                                    "start": w.start,
                                    "end": w.end
                                })

                        # Detect non-English segments (potential DAI ads).
                        # Whisper segments don't have per-segment language, but we can
                        # detect non-English by checking for non-ASCII characters or
                        # using the overall detected language with segment analysis.
                        segment_text = segment.text.strip()
                        is_foreign = should_detect_foreign and self._detect_non_english_segment(
                            segment_text, detected_lang
                        )

                        segment_dict = {
                            "start": segment.start,
                            "end": segment.end,
                            "text": segment_text,
                            "words": words  # Word timestamps for boundary refinement
                        }

                        # Flag non-English segments for ad detection
                        if is_foreign:
                            segment_dict["is_foreign_language"] = True
                            segment_dict["detected_language"] = "non-english"
                            non_english_count += 1

                        result.append(segment_dict)

                        # Log progress every 10 segments
                        if segment_count % 10 == 0:
                            progress_min = segment.end / 60
                            logger.info(f"Transcription progress: {segment_count} segments, {progress_min:.1f} minutes processed")

                        # Log every 30 seconds of audio processed
                        if segment.end - last_log_time > 30:
                            last_log_time = segment.end
                            # Log the last segment's text (truncated)
                            text_preview = segment.text.strip()[:100] + "..." if len(segment.text.strip()) > 100 else segment.text.strip()
                            logger.info(f"[{format_vtt_timestamp(segment.start)}] {text_preview}")

                    # Filter out hallucinations
                    original_count = len(result)
                    result = self.filter_hallucinations(result)
                    if len(result) < original_count:
                        logger.info(f"Filtered {original_count - len(result)} hallucination segments")

                    # Log non-English segments (potential DAI ads)
                    if non_english_count > 0:
                        logger.info(f"Flagged {non_english_count} non-English segments as potential ads")

                    duration_min = result[-1]['end'] / 60 if result else 0
                    logger.info(f"Transcription completed: {len(result)} segments, {duration_min:.1f} minutes")

                    if device == "cuda" and batch_size < tier_batch_size:
                        # Completing below the tier (downshift, clamp, or probe)
                        # proves the size fits; failures never persist anything.
                        self.record_batch_size_ceiling(batch_size)

                    self._record_local_stats('success', batch_size, retry_count,
                                             device, current_model)

                    return result

                except Exception as inner_e:
                    error_str = str(inner_e).lower()
                    is_oom = 'out of memory' in error_str

                    if ('cuda' in error_str or is_oom) and retry_count < max_retries - 1:
                        retry_count += 1
                        if is_oom:
                            old_batch_size = batch_size
                            batch_size = max(1, batch_size // 2)
                            logger.warning(
                                f"CUDA OOM detected (attempt {retry_count}/{max_retries}). "
                                f"Reducing batch size: {old_batch_size} -> {batch_size}"
                            )
                        else:
                            # Only an OOM says the size does not fit; anything
                            # else is retried as-is so it cannot pin a ceiling.
                            logger.warning(
                                f"CUDA error (attempt {retry_count}/{max_retries}), "
                                f"retrying at batch size {batch_size}: {inner_e}"
                            )
                        reload_model = True
                        continue
                    # Non-OOM error or max retries reached
                    raise

        except Exception as e:
            logger.error(f"Transcription failed: {e}")
            self._record_local_stats('failed', batch_size, retry_count, device,
                                     current_model, error=str(e))
            # Clean up GPU memory on ANY failure to prevent memory leaks
            # This is critical for OOM recovery - free memory before retry
            try:
                clear_gpu_memory()
                WhisperModelSingleton.unload_model()
                logger.info("Cleaned up GPU memory after transcription failure")
            except Exception as cleanup_err:
                logger.warning(f"Failed to clean up GPU memory: {cleanup_err}")
            if isinstance(e, ModelLoadError):
                raise
            return None
        finally:
            if gpu_admission_acquired:
                _gpu_admission_release()
            if override_token is not None:
                _local_model_override.reset(override_token)
            # Clean up preprocessed file
            if preprocessed_path and os.path.exists(preprocessed_path):
                try:
                    os.unlink(preprocessed_path)
                    logger.debug(f"Cleaned up preprocessed file: {preprocessed_path}")
                except OSError:
                    pass

    def _run_chunk_plan(
        self,
        plan_subset: list[tuple[int, float, float]],
        whisper_settings: dict[str, str],
        results: list[list[dict] | None],
        connectivity_errors: list[Exception],
        extraction_failures: list[int],
        extraction_timeouts: list[int],
        audio_path: str,
        language_override: str | None,
        prefix: str,
        max_workers: int,
        max_failed_chunks: int,
        stop_on_connectivity_error: bool,
    ):
        """Submit plan_subset to a fresh executor, filling results[idx] in place.
        Returns a chunk index to trigger failover, _ABORT_CHUNK_PLAN on a blown
        budget with no switch to make, or None when the subset finishes; raises
        ServiceUnavailableError/AudioExtractionError/AudioExtractionTimeout otherwise (#806)."""
        _note_whisper_settings(whisper_settings)
        extract_as_flac = not bool(whisper_settings.get('skip_flac_compression', False))

        def _process_chunk(chunk_idx: int, c_start: float, c_end: float):
            try:
                chunk_path = extract_audio_chunk(
                    audio_path, c_start, c_end,
                    preprocess=True, flac=extract_as_flac,
                )
            except AudioExtractionTimeout as e:
                logger.error(f"{prefix}Chunk {chunk_idx + 1}: {e}")
                extraction_failures.append(chunk_idx)
                extraction_timeouts.append(chunk_idx)
                return chunk_idx, None
            if not chunk_path:
                logger.error(f"{prefix}Chunk {chunk_idx + 1}: ffmpeg extract failed")
                extraction_failures.append(chunk_idx)
                return chunk_idx, None
            try:
                segs = self._transcribe_via_api(
                    chunk_path, whisper_settings,
                    language_override=language_override,
                    preprocessed=True,
                )
                if segs is None:
                    return chunk_idx, None
                # Adjust timestamps to be relative to full audio
                for seg in segs:
                    seg['start'] += c_start
                    seg['end'] += c_start
                    if seg.get('words'):
                        for word in seg['words']:
                            word['start'] += c_start
                            word['end'] += c_start
                return chunk_idx, segs
            except (ServiceUnavailableError, TranscriptionRejectedError) as e:
                # list.append is thread-safe; recorded so an endpoint-down
                # abort can defer the episode (#482) or trigger failover (#806)
                # instead of failing it.
                logger.error(f"{prefix}Chunk {chunk_idx + 1} failed: {e}")
                connectivity_errors.append(e)
                return chunk_idx, None
            except Exception as e:
                logger.error(f"{prefix}Chunk {chunk_idx + 1} failed: {e}")
                return chunk_idx, None
            finally:
                _unlink_quiet(chunk_path)

        failed = 0
        # Managed manually (not `with`) so an early abort can return promptly:
        # a `with` block's __exit__ calls shutdown(wait=True), which would
        # re-block on the in-flight workers and defeat the short-circuit.
        exe = ThreadPoolExecutor(max_workers=max_workers)
        # Baseline before any chunk completes: a future only reaches as_completed()
        # after _process_chunk appends, so reading this later would double-count it.
        seen_connectivity = len(connectivity_errors)
        try:
            futures = [
                exe.submit(run_in_worker_thread(_process_chunk), i, s, e)
                for i, s, e in plan_subset
            ]
            for completed, fut in enumerate(as_completed(futures), 1):
                chunk_idx, segs = fut.result()
                results[chunk_idx] = segs
                if segs is None:
                    failed += 1
                logger.info(
                    f"{prefix}Chunk {chunk_idx + 1} complete "
                    f"({completed}/{len(plan_subset)}): "
                    f"{len(segs) if segs else 0} segments"
                )
                # A connectivity failure switches backend immediately rather
                # than waiting on the failure budget below (#806).
                if (stop_on_connectivity_error and segs is None
                        and len(connectivity_errors) > seen_connectivity):
                    return chunk_idx
                seen_connectivity = len(connectivity_errors)
                # Short-circuit like the sequential path: once the failure
                # budget is blown the run can't succeed, so stop instead of
                # burning a full HTTP timeout on each remaining doomed chunk.
                # Returning here still runs the finally below (pool shutdown).
                if failed > max_failed_chunks:
                    logger.error(
                        f"{prefix}Too many failed chunks ({failed} > {max_failed_chunks}); "
                        f"aborting transcription early"
                    )
                    # Classify the abort as an outage only when connectivity
                    # failures are the majority cause; a single timeout among
                    # mostly content/request failures must not defer the
                    # episode (#482). The reverse mix falls back to the normal
                    # transient-retry ladder, whose next attempt re-classifies.
                    if len(connectivity_errors) * 2 > failed:
                        raise connectivity_errors[0]
                    # Name local extraction as the cause when it dominates
                    # (#556): "Failed to transcribe audio" points users at a
                    # healthy transcription provider.
                    if len(extraction_failures) * 2 > failed:
                        if len(extraction_timeouts) * 2 > len(extraction_failures):
                            raise AudioExtractionTimeout(
                                'Audio chunk extraction timed out (ffmpeg ran '
                                'out of time preparing chunks); the '
                                'transcription API was not the problem')
                        raise AudioExtractionError(
                            'Audio chunk extraction failed (ffmpeg could not '
                            'decode the source file); the transcription API '
                            'was not the problem')
                    return _ABORT_CHUNK_PLAN
        finally:
            # wait=False: return without blocking on in-flight workers (they
            # finish in the background and self-clean temp files via
            # _process_chunk's finally). cancel_futures drops queued chunks.
            exe.shutdown(wait=False, cancel_futures=True)
        return None

    def _transcribe_chunked_parallel_api(
        self,
        audio_path: str,
        duration: float,
        whisper_settings: dict[str, str],
        language_override: str | None = None,
        allow_failover: bool = True,
    ) -> list[dict] | None:
        """Parallel chunked transcription for remote API backends.

        Submits all chunks to a ThreadPoolExecutor; preserves chronological
        ordering at merge time so merge_overlapping_segments dedupes the
        overlap zone exactly as in the sequential path. Failure tolerance
        matches the sequential loop (~20% chunks may fail before abort).

        A connectivity failure (ServiceUnavailableError/TranscriptionRejectedError)
        triggers an immediate switch to the failover whisper config when one
        is configured (#806): the same backend type reruns only the chunks
        still missing a result on it, keeping whatever this pass already
        finished; a different backend type discards partial results and
        reruns the whole episode via _transcribe_chunked_local.
        """
        chunk_settings = _get_chunk_settings()
        chunk_duration = chunk_settings['max_chunk_seconds']
        overlap = chunk_settings['chunk_overlap_seconds']
        max_workers = get_pool().chunk_workers(chunk_settings['concurrent_chunks'])
        prefix = _log_prefix()

        # Computed once up front (not only for the chunk-plan path below) so
        # the single-shot branch can also switch on an outage instead of
        # just propagating it (#806).
        can_switch_on_outage = False
        if allow_failover and not whisper_settings.get('is_failover'):
            try:
                # Inline import: see active_whisper_settings for the cycle reason.
                import failover
                can_switch_on_outage = failover.is_configured(failover.TARGET_WHISPER)
            except Exception as e:
                logger.warning(f"Could not check whisper failover configuration: {e}")

        # Single-shot if entire audio fits in one chunk
        if duration <= chunk_duration:
            logger.info(
                f"Audio duration {duration/60:.1f}min fits in one chunk "
                f"({chunk_duration}s), single-shot API transcription"
            )
            try:
                return self.transcribe(audio_path, language_override=language_override,
                                       whisper_settings=whisper_settings)
            except (ServiceUnavailableError, TranscriptionRejectedError) as e:
                if not can_switch_on_outage:
                    raise
                _trigger_whisper_failover(e)
                fo = _get_failover_whisper_settings()
                if fo['backend'] != whisper_settings['backend']:
                    return self._transcribe_chunked_local(audio_path, duration, fo, language_override)
                return self.transcribe(audio_path, language_override=language_override,
                                       whisper_settings=fo)

        # Build chunk plan: list of (idx, start, end_with_overlap)
        plan: list[tuple[int, float, float]] = []
        chunk_start = 0.0
        idx = 0
        while chunk_start < duration:
            chunk_end = min(chunk_start + chunk_duration, duration)
            chunk_end_with_overlap = (
                min(chunk_end + overlap, duration)
                if chunk_end < duration else chunk_end
            )
            plan.append((idx, chunk_start, chunk_end_with_overlap))
            idx += 1
            chunk_start = chunk_end

        num_chunks = len(plan)
        max_failed_chunks = max(1, num_chunks // 5)
        logger.info(
            f"Starting parallel chunked transcription: {duration/60:.1f} min "
            f"in {num_chunks} chunks (chunk_size={chunk_duration}s, "
            f"overlap={overlap}s, workers={max_workers})"
        )

        connectivity_errors: list[Exception] = []
        # Chunk indexes whose ffmpeg extract failed (before any API call).
        # list.append is thread-safe; used so an abort can name local
        # extraction as the cause instead of the generic transcription
        # failure that sent #556's reporter debugging a healthy provider.
        extraction_failures: list[int] = []
        # Subset of the above that ran out of clock rather than failing to
        # decode, so the abort message does not blame the source file (#644).
        extraction_timeouts: list[int] = []
        results: list[list[dict] | None] = [None] * num_chunks

        # Class-qualified: some callers pass a duck-typed self.
        outage = Transcriber._run_chunk_plan(
            self, plan, whisper_settings, results, connectivity_errors,
            extraction_failures, extraction_timeouts, audio_path, language_override,
            prefix, max_workers, max_failed_chunks,
            stop_on_connectivity_error=can_switch_on_outage,
        )

        if isinstance(outage, int):
            _trigger_whisper_failover(connectivity_errors[0])
            fo = _get_failover_whisper_settings()
            if fo['backend'] != whisper_settings['backend']:
                # Different backend type: discard the partial chunk results
                # and rerun the whole episode on the failover backend.
                return self._transcribe_chunked_local(audio_path, duration, fo, language_override)
            # Same backend type: rerun only chunks still missing a result,
            # keeping what this pass finished. No further switch, already failover.
            remaining = [(i, s, e) for i, s, e in plan if results[i] is None]
            # Fresh error lists: the second pass classifies on its own failures,
            # and first-pass stragglers keep appending to the old ones.
            second = Transcriber._run_chunk_plan(
                self, remaining, fo, results, [], [], [], audio_path, language_override,
                prefix, max_workers, max_failed_chunks,
                stop_on_connectivity_error=False,
            )
            if second is _ABORT_CHUNK_PLAN:
                return None
        elif outage is _ABORT_CHUNK_PLAN:
            return None

        # Merge in chronological order so merge_overlapping_segments
        # dedupes overlap zones the same way the sequential path does.
        all_segments: list[dict] = []
        failed_chunks: list[tuple[float, float]] = []
        for chunk_idx, c_start, c_end in plan:
            chunk_segs = results[chunk_idx]
            if chunk_segs is None:
                failed_chunks.append((c_start, c_end))
                continue
            if not all_segments:
                all_segments = chunk_segs
            else:
                all_segments = merge_overlapping_segments(
                    all_segments, chunk_segs, c_start, overlap
                )

        # Both passes above already abort (return None) once their failure
        # budget is blown, so by here failures are within budget; the
        # surviving failed_chunks only feed the gap-summary log below.
        original_count = len(all_segments)
        all_segments = self.filter_hallucinations(all_segments)
        if len(all_segments) < original_count:
            logger.info(
                f"Filtered {original_count - len(all_segments)} "
                f"hallucination segments after merge"
            )

        duration_min = all_segments[-1]['end'] / 60 if all_segments else 0
        if failed_chunks:
            gap_summary = ", ".join(
                f"{s/60:.1f}-{e/60:.1f}min" for s, e in failed_chunks
            )
            logger.warning(
                f"Parallel chunked transcription complete with "
                f"{len(failed_chunks)} gap(s): {gap_summary}. "
                f"Partial transcript returned."
            )
        logger.info(
            f"Parallel chunked transcription complete: "
            f"{len(all_segments)} segments, {duration_min:.1f} minutes "
            f"from {num_chunks} chunks"
        )
        _warn_if_word_timestamps_missing(all_segments, whisper_settings)
        return all_segments

    def transcribe_chunked(
        self,
        audio_path: str,
        language_override: str | None = None,
        whisper_settings: dict[str, str] | None = None,
    ) -> list[dict]:
        """Transcribe audio files with dynamic chunking to prevent OOM errors.

        Branches to the parallel API path or the local chunked path by
        backend; see each for its own chunking strategy.

        Args:
            audio_path: Path to the audio file to transcribe
            language_override: Optional per-feed language; when set, takes
                precedence over the global whisper_language setting for this
                call only (forwarded to each chunk's transcribe()).
            whisper_settings: Pre-resolved settings (#806); when omitted,
                resolves the active/failover config.

        Returns:
            List of transcript segments with timestamps, or None on failure
        """
        duration = self.get_audio_duration(audio_path)
        if duration is None:
            logger.error("Cannot determine audio duration for chunked transcription")
            return None

        # Branch to parallel path for remote API backends - no GPU or
        # WhisperModelSingleton constraints, so chunks can run concurrently.
        whisper_settings = whisper_settings or active_whisper_settings()
        if whisper_settings['backend'] == WHISPER_BACKEND_API:
            with get_pool().transcribing():
                return self._transcribe_chunked_parallel_api(
                    audio_path, duration, whisper_settings,
                    language_override=language_override,
                )
        return self._transcribe_chunked_local(audio_path, duration, whisper_settings, language_override)

    def _transcribe_chunked_local(
        self,
        audio_path: str,
        duration: float,
        whisper_settings: dict[str, str],
        language_override: str | None = None,
    ) -> list[dict] | None:
        """Local chunked transcription; a trigger error reruns the whole episode
        on the failover whisper config, once (#806)."""
        _note_whisper_settings(whisper_settings)
        failover_local_model = (
            whisper_settings.get('local_model') if whisper_settings.get('is_failover') else None)
        # Set for the whole body so chunk sizing also sees the failover model.
        override_token = _local_model_override.set(failover_local_model) if failover_local_model else None
        try:
            _require_local_transcription()
            return self._transcribe_chunked_local_body(
                audio_path, duration, whisper_settings, language_override)
        except Exception as e:
            if not whisper_settings.get('is_failover') and is_whisper_failover_trigger(e):
                # Inline import: see active_whisper_settings for the cycle reason.
                import failover
                if failover.is_configured(failover.TARGET_WHISPER):
                    _trigger_whisper_failover(e)
                    return self.transcribe_chunked(
                        audio_path, language_override,
                        whisper_settings=_get_failover_whisper_settings())
            raise
        finally:
            if override_token is not None:
                _local_model_override.reset(override_token)

    def _transcribe_chunked_local_body(
        self,
        audio_path: str,
        duration: float,
        whisper_settings: dict[str, str],
        language_override: str | None = None,
    ) -> list[dict] | None:
        """_transcribe_chunked_local's chunking loop.

        1. Checks available memory and model size to calculate optimal chunk duration
        2. Processes audio in appropriately-sized chunks
        3. Catches OOM errors and retries with smaller chunks
        4. Clears GPU memory between chunks to limit peak usage
        """
        # Get current model and device for memory calculation
        model_name = WhisperModelSingleton.get_configured_model()
        device = resolve_whisper_device()

        # Calculate optimal chunk duration based on available memory
        chunk_duration, memory_reason = calculate_optimal_chunk_duration(
            model_name, device
        )
        overlap = CHUNK_OVERLAP_SECONDS

        # If calculated chunk can handle the entire audio, try regular transcription first
        if duration <= chunk_duration:
            logger.info(
                f"Audio duration {duration/60:.1f}min fits in calculated chunk "
                f"({chunk_duration/60:.0f}min), trying regular transcription"
            )
            try:
                result = self.transcribe(audio_path, language_override=language_override,
                                         whisper_settings=whisper_settings)
                if result is not None:
                    return result
                # If transcribe returns None but didn't raise, fall through to chunked
                logger.warning("Regular transcription returned None, falling back to chunked")
            except Exception as e:
                error_str = str(e).lower()
                if 'out of memory' in error_str or 'oom' in error_str or 'cuda' in error_str:
                    logger.warning(f"OOM during regular transcription, falling back to chunked: {e}")
                    # Reduce chunk size for chunked attempt
                    chunk_duration = max(CHUNK_MIN_DURATION_SECONDS, chunk_duration // 2)
                    clear_gpu_memory()
                    WhisperModelSingleton.unload_model()
                else:
                    raise

        # Calculate number of chunks
        num_chunks = max(1, int((duration - overlap) // (chunk_duration - overlap)) + 1)
        logger.info(
            f"Starting chunked transcription: {duration/60:.1f} min audio in ~{num_chunks} chunks "
            f"(chunk_size={chunk_duration/60:.0f}min, overlap={overlap}s) - {memory_reason}"
        )

        all_segments = []
        chunk_start = 0
        chunk_num = 0
        oom_retry_count = 0
        max_oom_retries = 3
        extract_timeout_retries = 0
        max_extract_timeout_retries = 1
        failed_chunks: list[tuple[float, float]] = []
        # Tolerate a minority of failed chunks (e.g. flaky remote Whisper API)
        # rather than aborting the whole episode. Cap at ~20% of expected chunks.
        max_failed_chunks = max(1, num_chunks // 5)

        # Extractions overlap GPU inference; close() reaps whatever is
        # still in flight on any exit path so no pool thread or temp
        # chunk file outlives the run.
        prefetcher = _ChunkPrefetcher(audio_path)
        try:
            # Start the first extractions now and load the model while they
            # run; done in sequence these are the two longest serial waits
            # of the pass. Load failures surface inside the loop, which
            # keeps its OOM handling.
            prefetcher.prime(chunk_start, chunk_duration, duration, overlap)
            try:
                WhisperModelSingleton.get_batched_pipeline()
            except Exception as e:
                logger.debug(f"Model warm-up deferred to first chunk: {e}")

            while chunk_start < duration:
                chunk_end = min(chunk_start + chunk_duration, duration)
                # Same helper the prefetcher keys on, so the lookup below
                # cannot drift from the bounds computed here.
                _, chunk_end_with_overlap = _chunk_bounds_ahead(
                    chunk_start, chunk_duration, duration, overlap, 1,
                )[0]

                # Recalculate num_chunks with current chunk_duration (may have changed due to OOM)
                remaining_duration = duration - chunk_start
                remaining_chunks = max(1, int((remaining_duration - overlap) // (chunk_duration - overlap)) + 1)

                logger.info(
                    f"Processing chunk {chunk_num + 1} (~{remaining_chunks} remaining): "
                    f"{chunk_start/60:.1f}-{chunk_end_with_overlap/60:.1f} min "
                    f"(chunk_size={chunk_duration/60:.0f}min)"
                )

                # Take this chunk from the prefetcher; it queues the next
                # chunks so their ffmpeg passes (preprocess filter folded
                # in) run while the GPU transcribes this one.
                try:
                    chunk_path = prefetcher.take(
                        chunk_start, chunk_duration, duration, overlap)
                except AudioExtractionTimeout:
                    # Re-running the same call would time out again, so shrink the
                    # chunk once before giving up, mirroring the OOM path (#644).
                    if extract_timeout_retries >= max_extract_timeout_retries:
                        raise
                    extract_timeout_retries += 1
                    chunk_duration = max(CHUNK_MIN_DURATION_SECONDS, chunk_duration // 2)
                    logger.warning(
                        f"Chunk {chunk_num + 1} extraction timed out; retrying with "
                        f"chunk_size={chunk_duration/60:.0f}min"
                    )
                    continue
                if not chunk_path:
                    logger.error(f"Failed to extract chunk {chunk_num + 1}")
                    # Name local extraction as the cause (#556): the generic
                    # transcription-failure message points at the wrong layer.
                    raise AudioExtractionError(
                        'Audio chunk extraction failed (ffmpeg could not decode '
                        'the source file); transcription never started')

                try:
                    # Transcribe chunk (will handle its own batch sizing and retries)
                    chunk_segments = self.transcribe(
                        chunk_path,
                        language_override=language_override, preprocessed=True,
                        whisper_settings=whisper_settings,
                    )

                    if chunk_segments is None:
                        error = (self.last_transcription_stats or {}).get('error', '')
                        if 'cuda' in error.lower() or 'out of memory' in error.lower():
                            raise RuntimeError(
                                f"Local CUDA transcription failed: {error}")
                        failed_chunks.append((chunk_start, chunk_end_with_overlap))
                        logger.error(
                            f"Chunk {chunk_num + 1} transcription failed "
                            f"({chunk_start/60:.1f}-{chunk_end_with_overlap/60:.1f} min); "
                            f"leaving transcript gap and continuing"
                        )
                        if len(failed_chunks) > max_failed_chunks:
                            logger.error(
                                f"Too many failed chunks ({len(failed_chunks)} > {max_failed_chunks}); "
                                f"aborting transcription"
                            )
                            return None
                        chunk_num += 1
                        chunk_start = chunk_end
                        continue

                    # Reset OOM retry count on success
                    oom_retry_count = 0

                    # Adjust timestamps to be relative to full audio
                    for seg in chunk_segments:
                        seg['start'] += chunk_start
                        seg['end'] += chunk_start
                        # Adjust word timestamps too if present
                        if seg.get('words'):
                            for word in seg['words']:
                                word['start'] += chunk_start
                                word['end'] += chunk_start

                    # Merge with existing segments, handling overlap deduplication
                    if not all_segments:
                        all_segments = chunk_segments
                    else:
                        all_segments = merge_overlapping_segments(
                            all_segments, chunk_segments, chunk_start, overlap
                        )

                    # Increment chunk counter only after successful processing
                    chunk_num += 1

                    logger.info(
                        f"Chunk {chunk_num} complete: {len(chunk_segments)} segments "
                        f"(total: {len(all_segments)})"
                    )

                    # Move to next chunk
                    chunk_start = chunk_end

                except Exception as e:
                    error_str = str(e).lower()
                    is_oom = 'out of memory' in error_str or 'oom' in error_str or 'cuda' in error_str

                    if is_oom and oom_retry_count < max_oom_retries:
                        oom_retry_count += 1
                        old_chunk_duration = chunk_duration
                        chunk_duration = max(CHUNK_MIN_DURATION_SECONDS, chunk_duration // 2)

                        logger.warning(
                            f"OOM on chunk {chunk_num + 1} (attempt {oom_retry_count}/{max_oom_retries}). "
                            f"Reducing chunk size: {old_chunk_duration/60:.0f}min -> {chunk_duration/60:.0f}min"
                        )

                        # Clean up and retry this chunk with smaller size
                        clear_gpu_memory()
                        WhisperModelSingleton.unload_model()
                        # Don't advance chunk_start - retry from same position
                        continue
                    # Non-OOM error or max retries reached
                    logger.error(f"Chunk {chunk_num + 1} failed: {e}")
                    raise

                finally:
                    # Clean up chunk file
                    _unlink_quiet(chunk_path)

                    # Clear GPU cache between chunks (no reload cost)
                    clear_gpu_memory()
                    logger.debug("Cleared GPU memory after chunk processing")
        finally:
            prefetcher.close()

        # Unload model once after all chunks complete to free VRAM
        # for downstream stages (audio analysis, fingerprinting, etc.)
        WhisperModelSingleton.unload_model()
        logger.info("Whisper model unloaded after chunked transcription")

        # Final hallucination filtering on merged results
        original_count = len(all_segments)
        all_segments = self.filter_hallucinations(all_segments)
        if len(all_segments) < original_count:
            logger.info(f"Filtered {original_count - len(all_segments)} hallucination segments after merge")

        duration_min = all_segments[-1]['end'] / 60 if all_segments else 0
        if failed_chunks:
            gap_summary = ", ".join(
                f"{s/60:.1f}-{e/60:.1f}min" for s, e in failed_chunks
            )
            logger.warning(
                f"Chunked transcription complete with {len(failed_chunks)} gap(s): "
                f"{gap_summary}. Partial transcript returned."
            )
        logger.info(
            f"Chunked transcription complete: {len(all_segments)} segments, "
            f"{duration_min:.1f} minutes from {chunk_num} chunks"
        )

        return all_segments

    def segments_to_text(self, segments: list[dict]) -> str:
        """Convert segments to readable text format."""
        lines = []
        for segment in segments:
            start_ts = format_vtt_timestamp(segment['start'])
            end_ts = format_vtt_timestamp(segment['end'])
            lines.append(f"[{start_ts} --> {end_ts}] {segment['text']}")
        return '\n'.join(lines)
