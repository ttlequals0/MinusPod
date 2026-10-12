"""Whisper settings, timeouts and compute-type resolution for the transcriber."""

import contextvars
import logging
import os
import database
import failover
import run_context
from config import (
    API_CHUNK_DURATION_SECONDS,
    WHISPER_BACKEND_LOCAL,
    WHISPER_BACKEND_API,
    WHISPER_COMPUTE_TYPES,
    WHISPER_COMPUTE_TYPE_DEFAULT,
    CHUNK_OVERLAP_SECONDS,
    HTTP_TIMEOUT_CONNECTION_TEST,
    CONNECTION_TEST_TIMEOUT_CEILING,
    HTTP_TIMEOUT_WHISPER,
    WHISPER_API_TIMEOUT_MIN,
    WHISPER_API_TIMEOUT_MAX,
    coerce_bool_setting,
    get_env_backed_int, MAX_AUDIO_DOWNLOAD_MB_MIN, MAX_AUDIO_DOWNLOAD_MB_ADVISORY,
)

logger = logging.getLogger('transcriber')


# Filter chain preprocess_audio applies; also folded into chunk extraction
# (extract_audio_chunk(preprocess=True)) so chunked paths run one ffmpeg pass
# per chunk instead of extract + preprocess.
PREPROCESS_AUDIO_FILTERS = 'loudnorm=I=-16:LRA=11:TP=-1.5,highpass=f=80,lowpass=f=8000'


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
        db = database.Database()
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
    """Return standby settings, inheriting blank language and max attempts."""
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
        db = database.Database()
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
            'failover_whisper_max_attempts', active['max_attempts'])
    except Exception as e:
        logger.warning(f"Could not read failover whisper settings from DB, using defaults: {e}")

    return defaults


def active_whisper_settings() -> dict[str, str]:
    """Use eligible active standby settings, otherwise the primary settings."""
    try:
        if (failover.is_active(failover.TARGET_WHISPER)
                and failover.is_configured(failover.TARGET_WHISPER)):
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


def _note_whisper_settings(whisper_settings: dict) -> None:
    """Mark the current run as having used the failover transcriber."""
    if whisper_settings.get('is_failover'):
        ctx = run_context.current()
        if ctx is not None:
            ctx.note_whisper_failover()


# Local-backend model override while running on the failover config (#806);
# consulted by WhisperModelSingleton.get_configured_model() ahead of the DB
# setting. Set only around a local transcription that uses failover settings.
_local_model_override: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    'whisper_local_model_override', default=None)


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
        db = database.Database()
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


def canonical_compute_type(raw: str | None) -> str:
    """The compute type the runtime loads for a raw setting value."""
    return _CANONICAL_COMPUTE_TYPES.get(raw or WHISPER_COMPUTE_TYPE_DEFAULT, WHISPER_COMPUTE_TYPE_DEFAULT)


def _get_whisper_compute_type() -> str:
    """Read WHISPER_COMPUTE_TYPE (DB preferred, env fallback), validated."""
    raw = os.environ.get('WHISPER_COMPUTE_TYPE', WHISPER_COMPUTE_TYPE_DEFAULT)
    try:
        db_value = database.Database().get_setting('whisper_compute_type')
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
