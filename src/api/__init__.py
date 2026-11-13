"""REST API for MinusPod web UI."""
import logging
import ipaddress
import math
import os
import re
import secrets
import time
from typing import Optional
from flask import Blueprint, abort, g, jsonify, request, send_file, session
from flask_limiter import Limiter, RateLimitExceeded
from flask_limiter.util import get_remote_address
from functools import wraps

import config
from config import normalize_model_key, LOCAL_UPLOAD_MAX_MB_DEFAULT
from api.auth_state import (
    AUTH_HOOK_EXEMPT_ENDPOINTS, note_missing_auth, password_is_set, resolve_auth,
)
from api.csrf import validate as csrf_validate
from llm_client import LLMDisabledError
from utils.validation import is_dangerous_slug, nullable_bool
from utils.http import client_ip, redact_feed_credentials, redact_log_text
# Import registers the memory-threadsafe scheme before the Limiter resolves it.
from utils.ratelimit_storage import (
    STORAGE_URI as RATELIMIT_STORAGE_URI,
    ensure_storage_available,
)
from utils.text import extract_text_in_range
from sponsor_service import SponsorService

logger = logging.getLogger('podcast.api')

# Track server start time for uptime calculation
# Stored in shared file so all gunicorn workers report the same uptime
def _server_run_owner():
    """Pid shared by every worker of one server run: the gunicorn master's parent pid,
    stable across a worker respawn; falls back to this pid when unforked."""
    parent = os.getppid()
    return str(parent if parent > 1 else os.getpid())


def _init_server_start_time():
    """Server start time for this run, shared across gunicorn workers so uptime doesn't
    flip on a worker respawn; a write failure is logged but non-fatal."""
    start_time = time.time()
    try:
        from status_service import StatusService
        return StatusService().claim_server_start_time(start_time, _server_run_owner())
    except Exception:
        logger.warning("Failed to record server start time in shared status file", exc_info=True)
    return start_time

_start_time = _init_server_start_time()

api = Blueprint('api', __name__, url_prefix='/api/v1')

# Per-worker in-process storage multiplies the effective limit by the worker count.
# Set RATE_LIMIT_STORAGE_URI=redis://<host>:6379 to share counters. The default is a
# thread-safe MemoryStorage subclass: stock memory:// can raise RuntimeError when two
# threads restart its expiry timer at once.
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["200 per minute", "1000 per hour"],
    storage_uri=os.environ.get('RATE_LIMIT_STORAGE_URI', RATELIMIT_STORAGE_URI),
)


def init_limiter(app):
    """Initialize rate limiter with Flask app.

    Deliberately not headers_enabled=True: flask-limiter would overwrite the explicit
    Retry-After values that routes set on JIT 503 responses with its own computed reset
    time. The frontend retry falls back to fixed backoff when a 429 has no Retry-After.
    """
    if os.environ.get('MINUSPOD_RATE_LIMIT_DISABLED', '').strip().lower() in ('true', '1', 'yes'):
        app.config['RATELIMIT_ENABLED'] = False
        logger.info("Rate limiter disabled by MINUSPOD_RATE_LIMIT_DISABLED")
        limiter.init_app(app)
        return
    limiter.init_app(app)
    storage_uri = os.environ.get('RATE_LIMIT_STORAGE_URI', RATELIMIT_STORAGE_URI)
    ensure_storage_available(storage_uri, limiter.storage)
    logger.debug("Rate limiter initialized: 200/min, 1000/hr default limits")


# Paths that don't require authentication. Exact matches only: a prefix like
# "/api/v1/auth/" would silently expose any future endpoint added under it.
AUTH_EXEMPT_PATHS = frozenset({
    '/api/v1/health',        # readiness probe
    '/api/v1/health/live',   # liveness probe
    '/api/v1/auth/status',   # used by the UI to decide whether to show login
    '/api/v1/auth/login',    # initial login
    '/api/v1/auth/logout',   # terminate session
    # First-time setup and rekey. The handler body-verifies `currentPassword` when one
    # is set. Do not add other /api/v1/auth/* endpoints here.
    '/api/v1/auth/password',
    # SSE: EventSource cannot surface a 401 to JS, so the browser reconnect-loops. The
    # status.py generator snapshots auth at connect and emits one `event: auth-failed`
    # message, which GlobalStatusBar.tsx turns into a redirect to /ui/login.
    # Exempt here so the generator runs; do not generalise to other endpoints.
    '/api/v1/status/stream',
})

# Strict exemption for cross-origin podcast-app artwork GETs: <img src> cannot handle
# a 401 auth dance. Enforces the 200-character slug limit; malformed paths fall
# through to the authenticated path and 401.
PODCAST_ARTWORK_PATH = re.compile(r'^/api/v1/feeds/([^/]+)/artwork$')


def _is_public_artwork_request(path, method):
    """Allow podcast artwork reads for canonical and safe legacy slugs."""
    if method not in ('GET', 'HEAD'):
        return False
    match = PODCAST_ARTWORK_PATH.fullmatch(path)
    if not match:
        return False
    slug = match.group(1)
    return len(slug) <= 200 and not is_dangerous_slug(slug)


def _remote_setup_allowed():
    if os.environ.get('MINUSPOD_REQUIRE_AUTH', 'false').lower() != 'true':
        return True
    try:
        if ipaddress.ip_address(request.remote_addr or '').is_loopback:
            return True
    except ValueError:
        pass
    expected = os.environ.get('MINUSPOD_SETUP_TOKEN', '')
    supplied = request.headers.get('X-MinusPod-Setup-Token', '')
    try:
        return bool(expected and secrets.compare_digest(supplied, expected))
    except TypeError:
        return False


@api.before_request
def check_auth():
    """Check authentication before each /api/v1/* request.

    Exemptions are exact-match or strict regex. Public feed serving (/<slug>, episode
    media, chapters.json) lives at app level and never reaches this function.
    """
    path = request.path

    if path == '/api/v1/auth/password':
        if not password_is_set(get_database()) and not _remote_setup_allowed():
            return error_response('Remote setup requires a setup token', 403)

    if request.endpoint in AUTH_HOOK_EXEMPT_ENDPOINTS:
        return None

    state = resolve_auth()
    if state is None:
        # Fail closed: a missing session check must never read as a real signed-out state.
        note_missing_auth()
        return error_response('Internal server error', 500)

    if path in AUTH_EXEMPT_PATHS:
        return None

    if _is_public_artwork_request(path, request.method):
        return None

    if not state.password_set:
        # No password set: the instance is intentionally open (no auth, no CSRF)
        # until a password is set under Settings > Security.
        if state.required:
            return error_response('Set the application password before using the API', 503)
        return None

    if not state.authenticated:
        return error_response('Authentication required', 401)

    # Double-submit CSRF check for mutating methods. SameSite=Strict on the session
    # cookie is the primary defense; the token header covers same-site edge cases.
    csrf_err = csrf_validate(request)
    if csrf_err:
        logger.warning("CSRF check failed path=%s method=%s ip=%s", path, request.method, request.remote_addr)
        return error_response(csrf_err, 403)

    return None


def local_upload_max_mb():
    """Configured per-request upload cap in MB."""
    return get_database().get_setting_int('local_upload_max_mb', LOCAL_UPLOAD_MAX_MB_DEFAULT)


@api.before_request
def _widen_upload_cap():
    # Audio uploads exceed the app-wide 10 MB cap; widen just these routes.
    # Werkzeug spools multipart parts >500 KB to disk, so nothing buffers
    # fully in memory.
    p = request.path
    if request.method == 'POST' and (
            p.endswith('/import/upload')
            or (p.startswith('/api/v1/feeds/') and p.endswith('/episodes'))):
        g.upload_limit_mb = local_upload_max_mb()
        request.max_content_length = g.upload_limit_mb * 1024 * 1024


def get_storage():
    """Get storage instance."""
    from storage import Storage
    return Storage()


def get_database():
    """Get database instance."""
    from database import Database
    return Database()


_UI_PATH_START = re.compile(r'(["\'])/ui/')


def apply_url_prefix(text: str) -> str:
    """Move baked /ui/ URLs under the prefix and tell the SPA and Swagger init the prefix via a meta tag."""
    prefix = config.URL_PREFIX
    if not prefix:
        return text
    # Quoted path starts only, so a third-party URL or text containing /ui/ is left alone.
    text = _UI_PATH_START.sub(lambda m: f'{m.group(1)}{prefix}/ui/', text)
    return text.replace('</head>', f'<meta name="minuspod-url-prefix" content="{prefix}"></head>', 1)


def public_path(path: str) -> str:
    """Prefix a root-relative path with the reverse-proxy URL prefix."""
    return f"{config.URL_PREFIX}{path}"


def get_feed_auth_key(db):
    """Active feed auth key or None. Lazy import: the api package loads
    before main_app finishes creating its singletons."""
    from main_app.feed_auth import active_feed_key
    return active_feed_key(db)


@api.url_value_preprocessor
def _guard_slug_param(_endpoint, values):
    """Reject dangerous slugs and malformed episode ids on every /api/v1/* route.

    Slug reads use is_dangerous_slug (accepts legacy uppercase/underscore URLs, blocks
    traversal); writes use strict is_valid_slug so a typo fails with 400. Episode ids
    use is_valid_episode_id (12-char hex); .isalnum() accepts Unicode lookalikes.
    Centralized so no route can forget it; ids in JSON bodies are validated per route.
    """
    if not values:
        return
    from utils.validation import (is_valid_slug, is_dangerous_slug,
                                  is_valid_episode_id)
    if 'episode_id' in values and not is_valid_episode_id(values['episode_id']):
        abort(400, description='invalid episode id')
    if 'slug' not in values:
        return
    slug = values['slug']
    method = request.method
    if method in ('GET', 'HEAD', 'OPTIONS'):
        if is_dangerous_slug(slug):
            abort(404, description='invalid slug')
    else:
        if not is_valid_slug(slug):
            abort(400, description='invalid slug')


def log_request(f):
    """Decorator to log API requests with detailed info (IP, user-agent, response time)."""
    @wraps(f)
    def decorated(*args, **kwargs):
        start_time = time.time()
        ip = client_ip()
        user_agent = request.headers.get('User-Agent', 'Unknown')[:100]

        try:
            result = f(*args, **kwargs)
            elapsed = (time.time() - start_time) * 1000  # ms
            status = result.status_code if hasattr(result, 'status_code') else 200
            path = redact_feed_credentials(request.path)
            logger.info(f"{request.method} {path} {status} {elapsed:.0f}ms [{ip}] [{user_agent}]")
            return result
        except Exception as e:
            elapsed = (time.time() - start_time) * 1000
            path = redact_feed_credentials(request.path)
            logger.error(f"{request.method} {path} ERROR {elapsed:.0f}ms [{ip}] - {e}")
            raise
    return decorated


from werkzeug.exceptions import HTTPException as _HTTPException
from werkzeug.exceptions import RequestEntityTooLarge as _RequestEntityTooLarge


@api.errorhandler(_RequestEntityTooLarge)
def _handle_request_too_large(exc):
    """413 on an upload route names the configured limit; elsewhere stays generic."""
    mb = g.get('upload_limit_mb')
    if mb is None:
        return _handle_http_exception(exc)
    return jsonify({
        'error': f'File is too large. This server accepts up to {mb} MB per upload.',
        'status': 413,
        'maxUploadMb': mb,
    }), 413


@api.errorhandler(_HTTPException)
def _handle_http_exception(exc):
    """Pass werkzeug HTTPException (abort(400), 404, etc.) through unchanged."""
    return jsonify({'error': exc.description, 'status': exc.code}), exc.code


def retry_after_ceil(wait):
    """Whole seconds to wait, rounded up and at least 1; None means 1."""
    return 1 if wait is None else max(1, math.ceil(wait))


def limiter_retry_after_seconds(request_limits, now):
    """Whole seconds until every breached limit has reset, at least 1."""
    resets = [lim.reset_at for lim in request_limits if lim.breached]
    return retry_after_ceil(max(resets) - now if resets else None)


def retry_after_response(message, seconds, status, *, in_body=False, extra=None):
    """Error response with a Retry-After header; in_body also adds retryAfterSeconds and any extra fields."""
    if in_body:
        response = json_response(
            {'error': message, 'status': status, 'retryAfterSeconds': seconds, **(extra or {})}, status)
    else:
        response = error_response(message, status)
    response.headers['Retry-After'] = str(seconds)
    return response


@api.errorhandler(RateLimitExceeded)
def _handle_rate_limited(exc):
    """The HTTPException 429 body plus when to retry, as a header and retryAfterSeconds."""
    seconds = limiter_retry_after_seconds(limiter.current_limits, time.time())
    return retry_after_response(exc.description, seconds, 429, in_body=True)


API_PATH_PREFIX = '/api/'


@api.app_errorhandler(RateLimitExceeded)
def _handle_app_rate_limited(exc):
    """A limiter 429 outside the blueprint: JSON under /api/, the stock page elsewhere, both with Retry-After."""
    if request.path.startswith(API_PATH_PREFIX):
        return _handle_rate_limited(exc)
    response = exc.get_response()
    response.headers['Retry-After'] = str(
        limiter_retry_after_seconds(limiter.current_limits, time.time()))
    return response


def register_api_routing_errors(app):
    """Answer unmatched paths and wrong methods under /api/ in JSON; other paths keep their pages."""
    def route_exists(methods):
        """Whether the path routes for one of these methods; a rejected int(max) id does not."""
        adapter = app.url_map.bind_to_environ(request.environ)
        for method in methods:
            try:
                adapter.match(method=method)
                return True
            except _HTTPException:
                continue
        return False

    def api_json(exc, message):
        if not request.path.startswith(API_PATH_PREFIX):
            return exc
        status = exc.code
        allowed = getattr(exc, 'valid_methods', None)
        # Werkzeug reports 405 for an id its converter rejects when another method's rule exists.
        if status == 405 and not route_exists(allowed or ()):
            status, message = 404, 'Not found'
            allowed = None
        response = jsonify({'error': message, 'status': status})
        response.status_code = status
        if allowed:
            response.headers['Allow'] = ', '.join(allowed)
        return response

    app.register_error_handler(404, lambda exc: api_json(exc, 'Not found'))
    app.register_error_handler(405, lambda exc: api_json(exc, 'Method not allowed'))


@api.errorhandler(Exception)
def _handle_uncaught_exception(_exc):
    """Return a sanitized 500; the traceback is logged server-side only."""
    logger.exception("Unhandled exception in API request")
    return jsonify({'error': 'Internal server error', 'status': 500}), 500


def json_response(data, status=200):
    """Create JSON response with proper headers."""
    response = jsonify(data)
    response.status_code = status
    return response


def paged_args(default_limit=50, max_limit=100):
    """The clamped ?limit and ?offset query args as a (limit, offset) pair."""
    limit = min(max(1, request.args.get('limit', default_limit, type=int)), max_limit)
    offset = max(0, request.args.get('offset', 0, type=int))
    return limit, offset


def image_response(path, content_type):
    """Serve a cached image with stat-based ETag/Last-Modified revalidation."""
    response = send_file(path, mimetype=content_type, conditional=True, max_age=0)
    response.headers['Cache-Control'] = 'no-cache'
    # Magic-number checked on write: forbid sniffing and any script load.
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Content-Security-Policy'] = "default-src 'none'"
    return response


def _redacted(value):
    if isinstance(value, str):
        return redact_log_text(value)
    if isinstance(value, dict):
        return {key: _redacted(item) for key, item in value.items()}
    return value


def coded_error_response(message, status, code):
    """Error body with a stable machine-readable `code` next to the message."""
    return json_response({'error': _redacted(message), 'status': status, 'code': code}, status)


def llm_disabled_response(message):
    """409 for an action that needs an LLM while the provider is none."""
    return coded_error_response(message, 409, 'llm_disabled')


@api.errorhandler(LLMDisabledError)
def _handle_llm_disabled(exc):
    return llm_disabled_response(str(exc))


def error_response(message, status=400, details=None):
    """Create error response. `details` is logged server-side and dropped from
    the client payload for 5xx, so internal state never leaks externally."""
    # Messages often carry exception text, which can hold a URL query or credential.
    message, details = _redacted(message), _redacted(details)
    data = {'error': message, 'status': status}
    if details:
        if status >= 500:
            logger.error(f"Internal error ({status}) details: {details}")
        else:
            data['details'] = details
    return json_response(data, status)


def _resolve_original_audio(db, storage, slug, episode_id, self_heal=False, episode=None):
    """Resolve an episode's retained original audio path.

    Returns ``(audio_path, None)`` or ``(None, error_response)`` when the episode is
    unknown, has no retained original, or the file is missing. With ``self_heal=True``
    a missing file also clears the stale ``original_file`` column; cue-template routes
    leave it off so a transiently unreadable file cannot permanently NULL the column.
    Pass ``episode`` when the caller already holds the row.
    """
    if episode is None:
        episode = db.get_episode(slug, episode_id)
    if not episode or not episode.get('original_file'):
        return None, error_response('Original audio not retained for this episode', 404)
    audio_path = storage.get_original_path(slug, episode_id)
    if not audio_path.exists():
        if self_heal:
            db.upsert_episode(slug, episode_id, original_file=None)
        return None, error_response('Original audio file missing', 404)
    return audio_path, None


# Alias for backward compatibility
def extract_transcript_segment(transcript: str, start: float, end: float) -> str:
    """Extract text from transcript between timestamps (delegates to utils.text)."""
    return extract_text_in_range(transcript, start, end)


def extract_sponsor_from_text(ad_text: str) -> str:
    """Extract sponsor name from ad text (delegates to SponsorService)."""
    return SponsorService.extract_sponsor_from_text(ad_text)


def _serialize_auto_process(value):
    """Convert API boolean/null to DB string for auto_process_override."""
    if value is True:
        return 'true'
    if value is False:
        return 'false'
    return None


def _deserialize_auto_process(value):
    """Convert DB string to API boolean/null for auto_process_override."""
    if value == 'true':
        return True
    if value == 'false':
        return False
    return None


def _serialize_nullable_bool(value):
    """API bool/null -> DB int 1/0/None for tri-state boolean columns
    where None means 'use the matching global default setting'."""
    if value is None:
        return None
    return 1 if value else 0


_deserialize_nullable_bool = nullable_bool


def _normalize_nullable_finite_float(value, field_name, lo, hi):
    """Validate a nullable float override within [lo, hi]; returns (db_value, error).

    None or '' clears the override. bool is rejected (would coerce to 1.0/0.0), as are
    NaN/inf (they pass range checks because NaN comparisons are False and SQLite binds
    NaN as NULL). Out-of-range or non-numeric values give a 400 error.
    """
    if value is None or value == '':
        return None, None
    if isinstance(value, bool):
        return None, f"{field_name} must be a number or null, not a boolean"
    try:
        fval = float(value)
    except (TypeError, ValueError):
        return None, f"{field_name} must be a number or null"
    import math
    if not math.isfinite(fval):
        return None, f"{field_name} must be a finite number"
    if fval < lo or fval > hi:
        return None, f"{field_name} must be between {lo} and {hi}"
    return fval, None


def get_sponsor_service():
    """Get sponsor service instance."""
    from sponsor_service import SponsorService
    return SponsorService(get_database())


def _get_version():
    """Get application version."""
    from utils.app_version import APP_VERSION
    return APP_VERSION


def get_status_service():
    """Get status service instance."""
    from status_service import StatusService
    return StatusService()


def _enrich_models_with_pricing(models: list) -> None:
    """Attach pricing info to a list of model dicts using match_key lookups, then sort."""
    try:
        db = get_database()
        pricing_rows = db.get_model_pricing()
        pricing_lookup = {p['matchKey']: p for p in pricing_rows}
        overrides = db.get_model_pricing_overrides()

        for model in models:
            override = db.get_model_pricing_override(
                model.get('id', ''), overrides=overrides)
            if override is not None:
                model['inputCostPerMtok'] = override['inputCostPerMtok']
                model['outputCostPerMtok'] = override['outputCostPerMtok']
                model['cacheReadCostPerMtok'] = override.get('cacheReadCostPerMtok')
                model['pricingSource'] = 'operator'
                continue
            key = normalize_model_key(model.get('id', ''))
            pricing = pricing_lookup.get(key)
            if pricing:
                model['inputCostPerMtok'] = pricing['inputCostPerMtok']
                model['outputCostPerMtok'] = pricing['outputCostPerMtok']
                model['cacheReadCostPerMtok'] = pricing.get('cacheReadCostPerMtok')
                model['pricingSource'] = pricing['source']
            else:
                logger.debug(
                    f"No pricing match for model '{model.get('id')}' "
                    f"(match_key='{key}')"
                )
    except Exception as e:
        logger.warning(f"Failed to enrich models with pricing: {e}")

    models.sort(key=lambda m: (m.get('name') or m.get('id', '')).lower())


def _find_similar_pattern(db, pattern_data: dict) -> dict | None:
    """Find an existing pattern similar to the import data."""
    # Look for exact sponsor match in same scope
    sponsor = pattern_data.get('sponsor')
    scope = pattern_data.get('scope')

    if not sponsor:
        return None

    existing = db.get_ad_patterns(scope=scope, active_only=False)
    for p in existing:
        if p.get('sponsor') == sponsor:
            return p

    return None


# Import all sub-modules to trigger route registration. `status` is aliased so
# the submodule name does not shadow the `status` parameter of json_response /
# error_response defined above.
from api import feeds, episodes, local_episodes, history, settings, system, config_transfer, patterns, pattern_cleanup, patterns_audio, sponsors, status as _status_routes, auth, search, podcast_search, stats, providers, tags, cue_templates, cue_detections, detections, podping, failover, youtube
