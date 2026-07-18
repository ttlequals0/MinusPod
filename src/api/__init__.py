"""REST API for MinusPod web UI."""
import logging
import os
import re
import time
from typing import Optional
from flask import Blueprint, abort, jsonify, request, session
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from functools import wraps

from config import normalize_model_key
from utils.http import client_ip
from utils.text import extract_text_in_range
from sponsor_service import SponsorService

logger = logging.getLogger('podcast.api')

# Track server start time for uptime calculation
# Stored in shared file so all gunicorn workers report the same uptime
def _init_server_start_time():
    """Initialize server start time in shared status file.

    Always writes the current time on module load (server start).
    This ensures uptime resets on deploy/container restart even when
    the status file persists. Multiple workers may race to write,
    but the difference is negligible (milliseconds). An exception
    writing to the shared file is non-fatal (uptime just stays
    worker-local) but is logged so operators see the regression.
    """
    start_time = time.time()
    try:
        from status_service import StatusService
        svc = StatusService()
        svc.set_server_start_time(start_time)
    except Exception:
        logger.warning("Failed to record server start time in shared status file", exc_info=True)
    return start_time

_start_time = _init_server_start_time()

api = Blueprint('api', __name__, url_prefix='/api/v1')

# memory:// storage is per-worker; with workers=2 the effective limit is
# 2x declared. Set RATE_LIMIT_STORAGE_URI=redis://<host>:6379 to share
# counters across workers and get exact declared limits.
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["200 per minute", "1000 per hour"],
    storage_uri=os.environ.get('RATE_LIMIT_STORAGE_URI', 'memory://'),
)


def init_limiter(app):
    """Initialize rate limiter with Flask app."""
    limiter.init_app(app)
    logger.debug("Rate limiter initialized: 200/min, 1000/hr default limits")


# Paths that don't require authentication. Every entry is an exact match;
# no prefixes or substring contains. A prefix like "/api/v1/auth/" is a
# footgun: any future endpoint added under it (e.g. /auth/setup-2fa)
# would be silently unauthenticated. Keeping this list closed helps
# reviewers see the full public surface at a glance.
AUTH_EXEMPT_PATHS = frozenset({
    '/api/v1/health',        # readiness probe
    '/api/v1/health/live',   # liveness probe
    '/api/v1/auth/status',   # used by the UI to decide whether to show login
    '/api/v1/auth/login',    # initial login
    '/api/v1/auth/logout',   # terminate session
    # First-time setup + self-service rekey. The handler body-verifies
    # `currentPassword` when one is already set, so an unauthenticated
    # caller with no prior password can bootstrap, while an existing
    # password requires possession of the current one. Do NOT add other
    # /api/v1/auth/* endpoints here -- the blueprint-prefix version of
    # this list was removed specifically because it was a footgun for
    # future auth endpoints.
    '/api/v1/auth/password',
    # SSE: EventSource cannot surface an HTTP 401 to the JavaScript
    # handler -- the browser silently reconnect-loops against the
    # closed response. The generator in status.py snapshots auth at
    # connect time and emits a single `event: auth-failed` SSE message,
    # which GlobalStatusBar.tsx listens for and redirects to /ui/login.
    # Exempt here so the generator runs at all; DO NOT generalise this
    # to other endpoints.
    '/api/v1/status/stream',
})

# Strict pattern exemption for podcast-app cross-origin artwork GETs.
# <img src> can't bounce through an auth dance on 401, so this one GET is
# public. The regex mirrors the strict slug shape (is_valid_slug); bad
# slugs fall through to the authenticated path and 401.
PODCAST_APP_EXEMPT_PATTERNS = (
    re.compile(r'^/api/v1/feeds/[a-z0-9][a-z0-9-]{0,63}/artwork$'),
)


@api.before_request
def check_auth():
    """Check authentication before each /api/v1/* request.

    Exemptions are all exact-match or strictly regex-matched. Public
    podcast-feed serving (/<slug>, /episodes/<slug>/<id>.mp3, .vtt,
    chapters.json) is at the app level, not under this blueprint, and
    doesn't reach this function.
    """
    path = request.path

    if path in AUTH_EXEMPT_PATHS:
        return None

    if request.method == 'GET':
        for pattern in PODCAST_APP_EXEMPT_PATTERNS:
            if pattern.match(path):
                return None

    # Check if password is set
    db = get_database()
    password_hash = db.get_setting('app_password')
    if not password_hash or password_hash == '':
        # No password set: the instance is intentionally unprotected and fully
        # functional, matching the optional-password design. Every endpoint is
        # served (no auth, no CSRF) until a password is set under Settings >
        # Security, at which point the session + CSRF checks below take over. A
        # no-password instance exposed to the network is fully open by design.
        return None

    # Check session
    if not session.get('authenticated', False):
        return error_response('Authentication required', 401)

    # Double-submit CSRF check for mutating methods. SameSite=Strict on
    # the session cookie is the primary defense; the token header is a
    # belt-and-suspenders layer for same-site edge cases (subdomain
    # takeover, CNAME trust, etc.).
    from api.csrf import validate as csrf_validate
    csrf_err = csrf_validate(request)
    if csrf_err:
        logger.warning("CSRF check failed path=%s method=%s ip=%s", path, request.method, request.remote_addr)
        return error_response(csrf_err, 403)

    return None


def get_storage():
    """Get storage instance."""
    from storage import Storage
    return Storage()


def get_database():
    """Get database instance."""
    from database import Database
    data_dir = (
        os.environ.get('DATA_DIR')
        or os.environ.get('DATA_PATH')
        or os.environ.get('MINUSPOD_DATA_DIR')
    )
    return Database(data_dir) if data_dir else Database()


def get_feed_auth_key(db):
    """Active feed auth key or None. Lazy import: the api package loads
    before main_app finishes creating its singletons."""
    from main_app.feed_auth import active_feed_key
    return active_feed_key(db)


@api.url_value_preprocessor
def _guard_slug_param(_endpoint, values):
    """Reject dangerous slugs and malformed episode ids on every /api/v1/*
    route that takes them.

    Slug reads use :func:`is_dangerous_slug` (accepts legacy uppercase /
    underscore subscription URLs while still blocking traversal).
    Slug writes use :func:`is_valid_slug` (strict canonical regex) so a
    typo'd slug fails at 400 instead of making it to storage. Public
    ``/<slug>`` RSS and ``/episodes/<slug>/...`` routes are registered
    at the app level and handled by the storage-layer slug guard instead.

    ``episode_id`` path params are checked with :func:`is_valid_episode_id`
    (12-char MD5 hex): a strict check is needed because ``.isalnum()``
    accepts Unicode lookalikes. Centralized here so no per-route check can
    be forgotten; episode ids arriving in JSON bodies are still validated
    at the route level.
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
            logger.info(f"{request.method} {request.path} {status} {elapsed:.0f}ms [{ip}] [{user_agent}]")
            return result
        except Exception as e:
            elapsed = (time.time() - start_time) * 1000
            logger.error(f"{request.method} {request.path} ERROR {elapsed:.0f}ms [{ip}] - {e}")
            raise
    return decorated


from werkzeug.exceptions import HTTPException as _HTTPException


@api.errorhandler(_HTTPException)
def _handle_http_exception(exc):
    """Pass werkzeug HTTPException (abort(400), 404, etc.) through unchanged."""
    return jsonify({'error': exc.description, 'status': exc.code}), exc.code


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


def error_response(message, status=400, details=None):
    """Create error response. `details` is logged server-side and dropped from
    the client payload for 5xx, so internal state never leaks externally."""
    data = {'error': message, 'status': status}
    if details:
        if status >= 500:
            logger.error(f"Internal error ({status}) details: {details}")
        else:
            data['details'] = details
    return json_response(data, status)


def _resolve_original_audio(db, storage, slug, episode_id, self_heal=False):
    """Resolve an episode's retained original audio path.

    Returns ``(audio_path, None)`` on success or ``(None, error_response)`` when
    the episode is unknown, has no retained original, or the file is missing.
    Shared by the original-audio/peaks routes and the cue-template routes, which
    all require the original (un-cut) audio. With ``self_heal=True``, a missing
    file also clears the stale ``original_file`` column (original-only retention
    sweeps before 2.52.0 could leave it set) so the UI stops offering actions
    that can only 404. Cue-template routes leave self-heal off: a transiently
    unreadable file must not permanently NULL the column.
    """
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
    """Extract text from transcript between timestamps.

    Delegates to utils.text.extract_text_in_range.
    """
    return extract_text_in_range(transcript, start, end)


def extract_sponsor_from_text(ad_text: str) -> str:
    """Extract sponsor name from ad text by looking for URLs and common patterns.

    Delegates to SponsorService.extract_sponsor_from_text (canonical implementation).
    """
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


def _deserialize_nullable_bool(value):
    """DB int 1/0/None -> API True/False/None for tri-state boolean
    columns. None preserves 'use global default' semantics on the wire."""
    if value is None:
        return None
    return bool(value)


def _normalize_nullable_finite_float(value, field_name, lo, hi):
    """Validate a nullable float override within [lo, hi].

    Returns (db_value, error).
    - None or '' clears the override (returns None, None).
    - bool is rejected (True/False would silently coerce to 1.0/0.0).
    - Non-finite values (NaN, inf) are rejected; they pass range checks
      because NaN comparisons are always False and SQLite binds NaN as NULL.
    - Out-of-range or non-numeric values are rejected with a 400 error.
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
    try:
        import sys
        from pathlib import Path
        # Add parent directory to path for version module
        parent_dir = str(Path(__file__).parent.parent.parent)
        if parent_dir not in sys.path:
            sys.path.insert(0, parent_dir)
        from version import __version__
        return __version__
    except ImportError:
        return 'unknown'


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

        for model in models:
            key = normalize_model_key(model.get('id', ''))
            pricing = pricing_lookup.get(key)
            if pricing:
                model['inputCostPerMtok'] = pricing['inputCostPerMtok']
                model['outputCostPerMtok'] = pricing['outputCostPerMtok']
                model['pricingSource'] = pricing['source']
            else:
                logger.debug(
                    f"No pricing match for model '{model.get('id')}' "
                    f"(match_key='{key}')"
                )
    except Exception as e:
        logger.warning(f"Failed to enrich models with pricing: {e}")

    models.sort(key=lambda m: (m.get('name') or m.get('id', '')).lower())


def _find_similar_pattern(db, pattern_data: dict) -> Optional[dict]:
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
from api import feeds, episodes, history, settings, system, patterns, sponsors, status as _status_routes, auth, search, podcast_search, stats, providers, tags, cue_templates, cue_detections, detections
