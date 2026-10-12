"""Shared stage helpers: status publishing, stage timing, settings reads, transient-error classification."""
import functools
import inspect
import logging
import os
import shutil
import tempfile
from contextlib import contextmanager
import requests
import requests.exceptions
from cancel import _check_cancel

from config import (
    MIN_CUT_CONFIDENCE, ALLOWED_AUDIO_BITRATES, ALLOWED_AUDIO_ENCODER_COMPRESSION_LEVELS,
    ModelNotConfiguredError,
)

from llm_client import (
    is_retryable_error, is_llm_api_error, is_limit_exceeded_error, ProviderRateLimitedError,
)

import run_context
from transcriber import CDN_REFUSED_PREFIX
from utils.errors import (
    AudioTooLargeError,
    LocalTranscriptionUnavailableError, ModelLoadError, ServiceUnavailableError,
    TranscriptionRejectedError,
)

from main_app.pipeline.shared import (db, status_service)

audio_logger = logging.getLogger('podcast.audio')


def _require_publication_owner(slug: str, episode_id: str) -> None:
    """Stop a run that cannot prove its durable lease before publication."""
    ctx = run_context.current()
    run_id = getattr(ctx, 'run_id', None)
    if run_id:
        _check_cancel(None, slug, episode_id, run_id)


@functools.lru_cache(maxsize=None)
def _method_accepts_run_id(method: str) -> bool:
    params = inspect.signature(getattr(status_service, method)).parameters
    return 'run_id' in params or any(
        p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())


def _publish_status(method: str, slug: str, episode_id: str, *args):
    _require_publication_owner(slug, episode_id)
    run_id = getattr(run_context.current(), 'run_id', None)
    fn = getattr(status_service, method)
    if run_id and _method_accepts_run_id(method):
        return fn(slug, episode_id, *args, run_id=run_id)
    return fn(slug, episode_id, *args)


@contextmanager
def _measure_run_stage(name: str):
    """Measure one stage when the caller is bound to a processing run."""
    ctx = run_context.current()
    if ctx is None:
        yield
        return
    with ctx.timing.measure(name):
        yield


def get_min_cut_confidence() -> float:
    """Get the minimum confidence threshold for cutting ads from audio.

    This is configurable via the 'min_cut_confidence' setting (aggressiveness slider).
    Lower = more aggressive (removes more potential ads)
    Higher = more conservative (removes only high-confidence ads)

    Default value is MIN_CUT_CONFIDENCE from config.py
    """
    try:
        value = db.get_setting('min_cut_confidence')
        if value:
            threshold = float(value)
            # Clamp to valid range
            return max(0.50, min(0.95, threshold))
    except (ValueError, TypeError):
        pass
    return MIN_CUT_CONFIDENCE


def resolve_audio_encode_settings() -> tuple[str, str]:
    """Global bitrate and libmp3lame compression level for an AudioProcessor."""
    bitrate = db.get_setting('audio_bitrate') or '128k'
    if bitrate not in ALLOWED_AUDIO_BITRATES:
        bitrate = '128k'
    compression_level = db.get_setting('audio_encoder_compression_level') or 'default'
    if compression_level not in ALLOWED_AUDIO_ENCODER_COMPRESSION_LEVELS:
        compression_level = 'default'
    return bitrate, compression_level


def is_transient_error(error: Exception) -> bool:
    """Determine if an error is transient (worth retrying) or permanent.

    Delegates LLM API error classification to llm_client.is_retryable_error(),
    then applies episode-processing-specific checks for network, OOM, CDN, and
    audio format errors.
    """
    # Unconfigured model is operator-fixable but never self-resolves on retry.
    if isinstance(error, ModelNotConfiguredError):
        return False

    # Endpoint-unreachable errors are transient by definition; with the
    # offline queue (#482) disabled this keeps today's retry behavior.
    if isinstance(error, ServiceUnavailableError):
        return True

    # Whisper API auth rejection: a retry sends the same bad credentials (#806).
    if isinstance(error, TranscriptionRejectedError) and error.status in (401, 403):
        return False

    # Provider spend/usage limits are terminal until the operator adds
    # credits or raises the limit (#491).
    if is_limit_exceeded_error(error):
        return False

    # Held 429s (#696) are transient throttles: the hold-enabled branch in
    # _handle_processing_failure intercepts them first; disabled, they fall
    # through to the legacy rate-limited retry path.
    if isinstance(error, ProviderRateLimitedError):
        return True

    # Oversized enclosures never shrink on retry; the operator can raise
    # MAX_AUDIO_DOWNLOAD_MB and reprocess (#493).
    if isinstance(error, AudioTooLargeError):
        return False

    # Missing local Whisper packages need an install or a backend change (#795).
    if isinstance(error, LocalTranscriptionUnavailableError):
        return False

    # A GPU too full to load the model frees up; an OOM mid-transcription stays permanent below.
    if isinstance(error, ModelLoadError):
        return True

    # Network/connection errors are transient
    if isinstance(error, (
        requests.exceptions.Timeout,
        requests.exceptions.ConnectionError,
        ConnectionError,
        TimeoutError,
    )):
        return True

    # Delegate LLM API error checks to the shared classifier
    if is_retryable_error(error):
        return True

    # Known LLM API error that wasn't retryable -- permanent
    if is_llm_api_error(error):
        return False

    # Permanent errors - don't retry
    if isinstance(error, (
        ValueError,
        FileNotFoundError,
        PermissionError,
        TypeError,
    )):
        return False

    # Check error message for patterns
    error_msg = str(error).lower()

    # OOM errors are PERMANENT - retrying without more RAM won't help
    oom_patterns = [
        'out of memory', 'oom', 'cuda out of memory',
        'cannot allocate memory', 'memory allocation failed',
        'killed', 'memoryerror', 'torch.cuda.outofmemoryerror',
    ]
    if any(pattern in error_msg for pattern in oom_patterns):
        return False

    # CDN errors are transient. A refusal of one User-Agent is not, and is
    # matched below; a 403 that ignores the identifier is a block that lifts.
    transient_patterns = [
        'cdn not ready', 'cdn timeout', 'cdn server error', 'cdn check failed',
        'cdn blocked',
    ]
    if any(pattern in error_msg for pattern in transient_patterns):
        return True

    # Permanent content/auth errors. 404 / "not found" is deliberately absent:
    # a freshly published episode 404s briefly while the host provisions the
    # media URL, so it is transient (the retry cap still fails a dead link).
    permanent_patterns = [
        'invalid audio', 'unsupported format', 'corrupt',
        'authentication', 'unauthorized', 'forbidden',
        '400 ', '401 ', '403 ',
        # A refusal answers the same way on every retry.
        CDN_REFUSED_PREFIX.lower(),
        # Local feeds have no upstream to retry against: a missing retained
        # original never recovers on its own, so retrying just burns the
        # full ladder before landing on permanently_failed anyway.
        'original audio missing',
    ]
    if any(pattern in error_msg for pattern in permanent_patterns):
        return False

    # Default: assume transient for unknown errors (safer to retry)
    return True


def _setting_float(db, key: str, default: float, allow_zero: bool = False) -> float:
    """Read a float setting with graceful fallback on missing/invalid values.

    allow_zero: accept 0 as a valid value (for settings where 0 = disabled).
    """
    value = db.get_setting(key)
    if value is None or value == '':
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        audio_logger.warning(f"Invalid float for setting {key!r}: {value!r}; using default {default}")
        return default
    if parsed > 0 or (allow_zero and parsed == 0):
        return parsed
    return default


def _refresh_rss_for_slug(slug, episode_id):
    """Force-refresh the RSS feed cache for ``slug``, logging on failure."""
    from main_app.feeds import get_feed_map, refresh_rss_feed
    try:
        feed_map = get_feed_map()
        if slug in feed_map:
            refresh_rss_feed(slug, feed_map[slug]['in'], force=True)
        from recents_feed import rebuild_recents_feed
        rebuild_recents_feed()
    except Exception as cache_err:
        audio_logger.warning(f"[{slug}:{episode_id}] Failed to regenerate RSS cache: {cache_err}")


def _copy_retained_original_to_temp(original_path):
    """Copy a retained original to a fresh temp file so the later retain-move and
    cleanup-unlink operate on the copy, never on the retained original. Returns
    the temp path."""
    fd, tmp_path = tempfile.mkstemp(suffix='.mp3')
    os.close(fd)
    shutil.copyfile(original_path, tmp_path)
    return tmp_path
