"""Remote transcription backend: health probes, endpoint probes, failover and the upload attempt policy.

request_transcription is the one home of the attempt policy (transport retries, 429 waits, word-timestamp and vad_filter fallbacks).
Tests patch transcriber.remote.safe_post for every Whisper API upload and probe; safe_get patches for probes target transcriber.remote.safe_get.
"""

import io
import logging
import tempfile
import os
import threading
import time
import failover
import run_context
import utils.audio
from utils.errors import (
    ServiceUnavailableError, LocalTranscriptionUnavailableError, ModelLoadError,
    TranscriptionRejectedError,
)
from utils.http import safe_url_for_log
from utils.connection_probe import run_probe, parse_probe_json, rejected_detail
from utils.rate_limit import parse_retry_after
from utils.safe_http import URLTrust, safe_get, safe_post
import utils.subprocess_registry
import requests
from whisper_pool import get_pool
from utils.ffmpeg_run import SAFE_MEDIA_INPUT_ARGS
from utils.ttl_cache import TTLCache
from config import (
    WHISPER_BACKEND_API,
    FFMPEG_SHORT_TIMEOUT,
    HTTP_MAX_REDIRECTS_API,
)
from transcriber import common, settings, local

logger = logging.getLogger('transcriber')


def get_remote_transcriber_health() -> dict:
    """API-backend transcriber health for /system/status, from cached probes
    only: a polled status endpoint must not fire an outbound request per
    tick. Unconfigured reads as unavailable; never probed reads as available
    (nothing has failed)."""
    whisper_settings = settings._get_whisper_settings()
    base_url = (whisper_settings.get('api_base_url') or '').rstrip('/')
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
    backend = settings._get_whisper_settings()['backend']
    if backend == WHISPER_BACKEND_API:
        return get_remote_transcriber_health()  # parakeet reads the in-process health below
    return local.get_local_transcriber_health(backend)


def _trigger_whisper_failover(original: Exception) -> None:
    """Trigger whisper failover; re-raise `original` when no failover ends up active, so the episode defers."""
    target = failover.TARGET_WHISPER
    if not failover.trigger(target, str(original)) and not failover.is_active(target):
        raise original


def _switch_to_failover_whisper(original: Exception) -> dict:
    """Trigger whisper failover and return the standby settings, logging the switch to the run log."""
    _trigger_whisper_failover(original)
    fo = settings._get_failover_whisper_settings()
    ctx = run_context.current()
    tag = f"[{ctx.key}] " if ctx is not None else ''
    target = (safe_url_for_log(fo['api_base_url']) if fo['backend'] == WHISPER_BACKEND_API
              else f"model {fo.get('local_model') or 'inherited'}")
    logger.warning(f"{tag}Transcription switching to failover transcriber {fo['backend']} {target}")
    return fo


def is_whisper_failover_trigger(exc: Exception) -> bool:
    """Errors that mean this transcriber config cannot serve the episode right now."""
    return isinstance(exc, (ServiceUnavailableError, TranscriptionRejectedError,
                            ModelLoadError, LocalTranscriptionUnavailableError))


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
    whisper_settings = None
    if base_url is None:
        whisper_settings = settings._get_whisper_settings()
        base_url = whisper_settings['api_base_url']
    if not base_url:
        return {'available': False}
    if api_key is None:
        api_key = whisper_settings['api_key'] if whisper_settings else ''

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


def _probe_upload(skip_flac_compression: bool) -> tuple[str, bytes]:
    """Probe audio in the format a real episode upload would use: FLAC by
    default, WAV when the skip toggle is on (mirrors _transcribe_via_api,
    including its fall-back to WAV when the FLAC encode fails). Matters for
    servers with no FLAC decoder (OVMS): a WAV-only probe would pass while
    real episodes fail.
    """
    wav = common.probe_wav_bytes()
    if skip_flac_compression:
        return 'probe.wav', wav
    wav_path = flac_path = None
    try:
        fd, wav_path = tempfile.mkstemp(suffix='.wav')
        with os.fdopen(fd, 'wb') as fh:
            fh.write(wav)
        fd, flac_path = tempfile.mkstemp(suffix='.flac')
        os.close(fd)
        encode = utils.subprocess_registry.tracked_run(
            ['ffmpeg', *SAFE_MEDIA_INPUT_ARGS, '-y', '-i', wav_path, '-c:a', 'flac', flac_path],
            capture_output=True, timeout=FFMPEG_SHORT_TIMEOUT,
        )
        if encode.returncode == 0 and os.path.getsize(flac_path) > 0:
            with open(flac_path, 'rb') as fh:
                return 'probe.flac', fh.read()
    except Exception as e:
        logger.warning(f"Probe FLAC encode failed, sending WAV: {e}")
    finally:
        common.unlink_quiet(wav_path)
        common.unlink_quiet(flac_path)
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
    probe_timeout = settings._connection_test_timeout()
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


def _post_once(url, headers, transcribe_path, form_data, whisper_settings, max_attempts):
    """POST one audio file with transport retries and pool-aware 429 waits.

    Returns (response, last_request_exc); response is None when every attempt
    failed at the transport layer. SSRFError propagates to the caller.
    """
    response = None
    last_request_exc = None
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
                        retry_deadline = now + settings._api_timeout(whisper_settings)
                    else:
                        retry_deadline += now - permit_wait_start
                    settings._note_whisper_settings(whisper_settings)
                    response = safe_post(
                        url,
                        trust=URLTrust.OPERATOR_CONFIGURED,
                        timeout=settings._api_timeout(whisper_settings),
                        max_redirects=HTTP_MAX_REDIRECTS_API,
                        files={'file': (os.path.basename(transcribe_path), audio_file)},
                        data=form_data,
                        headers=headers,
                    )
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
                    "retry deadline; giving up", settings._log_prefix())
                raise TranscriptionRejectedError(429, 'retry deadline exceeded')
            # Floor so a Retry-After: 0 (or absent) header still yields.
            parsed_retry_after = parse_retry_after(
                (response.headers or {}).get('Retry-After'), max_seconds=300.0)
            retry_after = max(
                parsed_retry_after if parsed_retry_after is not None else 5.0, 0.5)
            logger.warning(
                "%sWhisper API busy (429); waiting %.1fs before retrying",
                settings._log_prefix(), retry_after)
            time.sleep(retry_after)
            response = None
            continue
        if response.status_code < 500 and response.status_code != 408:
            break
        logger.warning(
            "Whisper API attempt %d/%d returned %d",
            attempt + 1, max_attempts, response.status_code,
        )
        attempt += 1
    return response, last_request_exc


def request_transcription(url, headers, transcribe_path, build_form_data, whisper_settings):
    """POST the audio to the Whisper API and return the final response.

    build_form_data(word_timestamps, vad_filter_allowed) returns the form dict.
    Retries segment-only when the server rejects word timestamps, and without
    vad_filter when it rejects that field. Raises ServiceUnavailableError when
    every attempt fails at the transport layer; SSRFError propagates.
    """
    max_attempts = int(whisper_settings.get('max_attempts') or 2)
    last_request_exc = None
    vad_filter_allowed = True
    while True:
        for word_timestamps in (True, False):
            form_data = build_form_data(word_timestamps, vad_filter_allowed)
            response, attempt_exc = _post_once(
                url, headers, transcribe_path, form_data, whisper_settings, max_attempts)
            if attempt_exc is not None:
                last_request_exc = attempt_exc
            if response is None:
                # Unreachable endpoint; the offline queue (#482) must tell it apart from a bad response.
                raise ServiceUnavailableError(
                    'whisper', f"Whisper API unreachable: {last_request_exc}")
            if response.status_code == 200:
                break
            if word_timestamps and _whisper_api_rejects_word_timestamps(response):
                logger.warning(
                    "Whisper API does not support word timestamps; "
                    "retrying with segment-only timestamps"
                )
                continue
            break

        if (response.status_code != 200 and vad_filter_allowed
                and 'vad_filter' in form_data
                and _whisper_api_rejects_vad_filter(response)):
            logger.warning("Whisper API rejected vad_filter field; retrying without it")
            vad_filter_allowed = False
            continue
        return response
