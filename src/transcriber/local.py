"""Local transcription backend: availability, GPU admission, model unload and health."""

import io
import json
import logging
import tempfile
import os
import threading
import time
from contextlib import contextmanager
from datetime import timedelta
import database
import run_context
from utils.errors import LocalTranscriptionUnavailableError, ModelLoadError
from utils.time import parse_iso_utc, utc_now, utc_now_iso
from utils.gpu import clear_gpu_memory, get_gpu_device_name, get_gpu_memory_info
import parakeet_transcriber
from parakeet_transcriber import ParakeetModelSingleton
from config import (
    LOCAL_MODEL_IDLE_UNLOAD_SECONDS,
    WHISPER_BACKEND_LOCAL,
    WHISPER_BACKEND_PARAKEET,
    WHISPER_COMPUTE_TYPE_FALLBACK_CHAIN,
    resolve_whisper_device,
)
from transcriber import common, settings
# Remote-only installs do not ship the local stack; keep the module importable
# so WHISPER_BACKEND=openai-api still works without it (#795).
try:
    import ctranslate2
    from faster_whisper import WhisperModel, BatchedInferencePipeline
    _LOCAL_IMPORT_ERROR: ImportError | None = None
except ImportError as e:
    ctranslate2 = WhisperModel = BatchedInferencePipeline = None
    _LOCAL_IMPORT_ERROR = e

logger = logging.getLogger('transcriber')


def _local_unavailable_message(backend: str) -> str:
    if backend == WHISPER_BACKEND_PARAKEET:
        return parakeet_transcriber.parakeet_unavailable_message()
    return (
        f"Local Whisper backend needs faster-whisper and ctranslate2 "
        f"({_LOCAL_IMPORT_ERROR}); install them or set WHISPER_BACKEND=openai-api "
        f"with WHISPER_API_BASE_URL and WHISPER_API_KEY"
    )


def local_transcription_available(backend: str) -> bool:
    if backend == WHISPER_BACKEND_PARAKEET:
        return parakeet_transcriber.parakeet_available()
    return _LOCAL_IMPORT_ERROR is None


def _require_local_transcription(backend: str) -> None:
    if not local_transcription_available(backend):
        raise LocalTranscriptionUnavailableError(_local_unavailable_message(backend))


# Limit concurrent CUDA decodes to protect shared model memory; CPU work is unaffected.
GPU_TRANSCRIBE_MAX_CONCURRENT = max(1, int(os.getenv('GPU_TRANSCRIBE_MAX_CONCURRENT', '1')))
_GPU_ADMISSION_SEMAPHORE = threading.Semaphore(GPU_TRANSCRIBE_MAX_CONCURRENT)

_local_activity = threading.Condition()
_local_decode_count = 0
_local_probe_active = False
_last_local_decode_at = time.monotonic()


@contextmanager
def _local_decode_activity():
    global _local_decode_count, _last_local_decode_at
    with _local_activity:
        _local_activity.wait_for(lambda: not _local_probe_active)
        _local_decode_count += 1
    try:
        yield
    finally:
        with _local_activity:
            _local_decode_count -= 1
            _last_local_decode_at = time.monotonic()
            _local_activity.notify_all()


@contextmanager
def _idle_local_probe():
    global _local_probe_active
    with _local_activity:
        admitted = not _local_probe_active and _local_decode_count == 0
        if admitted:
            _local_probe_active = True
    try:
        if not admitted:
            yield False
            return
        with _all_admission_permits() as free:
            yield free
    finally:
        if admitted:
            with _local_activity:
                _local_probe_active = False
                _local_activity.notify_all()


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


def _unload_local_models() -> bool:
    """Unload the Whisper and Parakeet singletons; True if either was loaded."""
    unloaded = False
    for singleton in (WhisperModelSingleton, ParakeetModelSingleton):
        if singleton.is_loaded():
            singleton.unload_model()
            unloaded = True
    return unloaded


def unload_whisper_if_idle() -> bool:
    """Unload the local model once no decode is active and none ran for LOCAL_MODEL_IDLE_UNLOAD_SECONDS."""
    with _idle_local_probe() as free:
        if not free:
            return False
        if time.monotonic() - _last_local_decode_at < LOCAL_MODEL_IDLE_UNLOAD_SECONDS:
            return False
        return _unload_local_models()


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
    if not record.get('device'):
        record['device'] = resolve_whisper_device()
    record['observed_at'] = utc_now().isoformat().replace('+00:00', 'Z')
    with _local_transcription_state_lock:
        _last_local_transcription_outcome = record
    try:
        database.Database().set_setting(LOCAL_OUTCOME_SETTING, json.dumps(record))
    except Exception as e:
        logger.debug(f"Could not persist local transcription outcome: {e}")


def _read_persisted_local_outcome() -> dict | None:
    """Last local outcome any worker persisted, or None if unreadable."""
    try:
        raw = database.Database().get_setting(LOCAL_OUTCOME_SETTING)
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


def _latest_local_outcome(device: str, backend: str) -> dict | None:
    """Newest persisted or in-process outcome for the device and backend."""
    with _local_transcription_state_lock:
        in_process = dict(_last_local_transcription_outcome) if _last_local_transcription_outcome else None
    candidates = [c for c in (in_process, _read_persisted_local_outcome())
                  if c and c.get('device') == device
                  and c.get('backend', WHISPER_BACKEND_LOCAL) == backend]
    if not candidates:
        return None
    return max(candidates, key=lambda c: (stamp.timestamp()
               if (stamp := parse_iso_utc(c.get('observed_at'))) else 0))


def get_local_transcriber_health(backend: str | None = None) -> dict:
    """Local-backend transcriber health for /system/status.

    The local model runs in-process with no endpoint to probe, so 'available'
    reflects the last transcription outcome (a GPU-OOM exhaustion reads as
    available=False). The outcome is read from shared state so any worker
    reports the latest one; no attempt yet reads as available.
    """
    device = resolve_whisper_device()
    backend = backend or settings._get_whisper_settings()['backend']
    if not local_transcription_available(backend):
        return {
            'backend': backend,
            'device': device,
            'available': False,
            'reason': _local_unavailable_message(backend),
            'lastOutcome': None,
        }
    outcome = _latest_local_outcome(device, backend)
    last_outcome = None
    if outcome is not None:
        last_outcome = {
            'status': outcome.get('outcome'),
            'device': outcome.get('device'),
            'backend': outcome.get('backend'),
            'observedAt': outcome.get('observed_at'),
        }
    return {
        'backend': backend,
        'device': device,
        'available': outcome is None or outcome.get('outcome') != 'failed',
        'lastOutcome': last_outcome,
    }


def probe_local_transcription(request_config: dict) -> dict:
    """Probe the captured local config without interrupting active decodes."""
    backend = request_config.get('backend', WHISPER_BACKEND_LOCAL)
    if not local_transcription_available(backend):
        return {'reachable': False, 'status': None, 'detail': 'Local stack unavailable'}
    load_config = {'model': request_config['local_model'], 'device': request_config['device'],
                   'compute_type': request_config.get('compute_type')}
    outcome = _latest_local_outcome(load_config['device'], backend)
    failed = (outcome and outcome.get('outcome') == 'failed'
              and outcome.get('model') in (None, load_config['model']))
    if not request_config.get('recover_runtime') and not failed:
        return {'reachable': True, 'status': None, 'detail': 'Local stack available'}
    # Only the background queue thread owns the model; a web request thread
    # loading one would hold VRAM it never frees, even on the leader process.
    if not run_context.in_background_thread():
        return {'reachable': None, 'status': None, 'deferred': True,
                'detail': 'Diagnostic decode runs in the background worker'}
    with _idle_local_probe() as free:
        if not free:
            return {'reachable': None, 'status': None, 'detail': 'Local transcription busy'}
        try:
            if backend == WHISPER_BACKEND_PARAKEET:
                with tempfile.NamedTemporaryFile(suffix='.wav') as probe_wav:
                    probe_wav.write(common.probe_wav_bytes())
                    probe_wav.flush()
                    parakeet_transcriber.decode(probe_wav.name, 'en')
            else:
                model, _pipeline = WhisperModelSingleton.get_instance(load_config=load_config)
                segments, _info = model.transcribe(
                    io.BytesIO(common.probe_wav_bytes()), language='en', beam_size=1, vad_filter=False)
                list(segments)
        except Exception as exc:
            logger.warning('Local recovery decode failed: %s', exc)
            return {'reachable': False, 'status': None, 'detail': f'Local decode failed: {type(exc).__name__}'}
    return {'reachable': True, 'status': None, 'detail': 'Local diagnostic decode succeeded',
            'local_outcome': {'outcome': 'success', 'backend': backend,
                              'model': load_config['model'], 'device': load_config['device'],
                              'compute_type': load_config['compute_type'],
                              'observed_at': utc_now().isoformat().replace('+00:00', 'Z')}}


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
    _load_config = None

    @classmethod
    def get_configured_model(cls) -> str:
        """Get the configured model: the failover override while set (#806),
        else the database setting."""
        override = settings._local_model_override.get()
        if override:
            return override
        try:
            db = database.Database()
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
            cls._load_config = None

            # Force garbage collection and clear CUDA cache
            clear_gpu_memory()
            logger.info("CUDA cache cleared")

    @classmethod
    def is_loaded(cls) -> bool:
        return cls._instance is not None or cls._base_model is not None

    @classmethod
    def get_instance(cls, *, load_config: dict | None = None) -> tuple[WhisperModel, BatchedInferencePipeline]:
        """Return the local model and pipeline, reloading for a captured probe config when needed."""
        requested_config = load_config or {
            'model': cls.get_configured_model(), 'device': resolve_whisper_device(),
            'compute_type': settings._get_whisper_compute_type()}
        if cls._instance is not None and (cls._needs_reload or cls._load_config != requested_config):
            cls.unload_model()

        if cls._instance is None:
            _require_local_transcription(WHISPER_BACKEND_LOCAL)
            model_size = requested_config['model']
            device = requested_config['device']
            configured_compute_type = requested_config['compute_type']

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
            cls._load_config = requested_config
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
        """Return the pipeline after validating the current requested configuration."""
        return cls.get_instance()[1]

    @classmethod
    def get_current_model_name(cls) -> str | None:
        """Get the name of the currently loaded model."""
        return cls._current_model_name


# Batch size tiers based on audio duration (in seconds)
# Longer episodes need smaller batches to avoid CUDA OOM
BATCH_SIZE_TIERS = [
    (60 * 60, 16),      # < 60 min: batch_size=16
    (90 * 60, 12),      # 60-90 min: batch_size=12
    (120 * 60, 8),      # 90-120 min: batch_size=8
]

# Largest batch size proven to fit this device's VRAM: recorded when a run
# completes below the duration tier, so later episodes start at a size that
# fits. After the TTL the next run probes one size up, so a transient OOM
# cannot pin every later episode for good.
BATCH_CEILING_SETTING = 'transcribe_batch_size_ceiling'
BATCH_CEILING_TTL_DAYS = 2


def batch_ceiling_device() -> str:
    """Device name the stored ceiling applies to; VRAM differs per GPU model."""
    return get_gpu_device_name()


def read_ceiling() -> dict | None:
    """Stored ceiling for this device as {'size', 'recorded_at'}, or None
    when unset, malformed, or recorded for a different device. recorded_at
    is None for pre-2.96.0 payloads, which read as expired."""
    try:
        raw = database.Database().get_setting(BATCH_CEILING_SETTING)
    except Exception as e:
        logger.debug(f"Could not read batch size ceiling: {e}")
        return None
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        parsed = None
    if not isinstance(parsed, dict) or parsed.get('device') != batch_ceiling_device():
        return None
    try:
        size = max(1, int(parsed['size']))
    except (KeyError, TypeError, ValueError):
        return None
    return {'size': size, 'recorded_at': parsed.get('recorded_at')}


def ceiling_expired(entry: dict) -> bool:
    recorded_at = parse_iso_utc(entry['recorded_at'])
    return (recorded_at is None
            or utc_now() - recorded_at > timedelta(days=BATCH_CEILING_TTL_DAYS))


def record_batch_size_ceiling(batch_size: int) -> None:
    """Persist batch_size as this device's ceiling. Called only after a
    completed run, since only completion proves a size fits; ratchets down
    against an unexpired ceiling and keeps its timestamp so it still ages out.
    """
    candidate = max(1, int(batch_size))
    entry = read_ceiling()
    live = entry if entry and not ceiling_expired(entry) else None
    value = min(live['size'], candidate) if live else candidate
    recorded_at = live['recorded_at'] if live and value == live['size'] else utc_now_iso()
    payload = json.dumps({
        'device': batch_ceiling_device(), 'size': value, 'recorded_at': recorded_at,
    })
    try:
        database.Database().set_setting(BATCH_CEILING_SETTING, payload)
    except Exception as e:
        logger.debug(f"Could not persist batch size ceiling: {e}")


def tier_batch_size(duration_seconds: float | None) -> int:
    """Batch size from audio duration alone; longer episodes need smaller batches."""
    if duration_seconds is None:
        return 8
    for threshold, batch_size in BATCH_SIZE_TIERS:
        if duration_seconds < threshold:
            return batch_size
    return 4


def get_batch_size_for_duration(duration_seconds: float | None) -> int:
    """Duration tier, clamped by an unexpired ceiling; an expired one is
    probed one size up."""
    tier = tier_batch_size(duration_seconds)
    entry = read_ceiling()
    if entry is None:
        return tier
    if ceiling_expired(entry):
        return min(tier, entry['size'] * 2)
    return min(tier, entry['size'])
