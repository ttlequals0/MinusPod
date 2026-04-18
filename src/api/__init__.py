"""REST API for MinusPod web UI."""
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from typing import Optional
from flask import Blueprint, abort, jsonify, request, Response, session
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from functools import wraps

from config import normalize_model_key
from utils.time import parse_timestamp
from utils.text import extract_text_in_range
from sponsor_service import SponsorService
from cancel import cancel_processing

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
    logger.info("Rate limiter initialized: 200/min, 1000/hr default limits")


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
        return None  # No password set, allow access

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
    return Database()


@api.url_value_preprocessor
def _guard_slug_param(_endpoint, values):
    """Reject dangerous slugs on every /api/v1/* route that takes one.

    Reads use :func:`is_dangerous_slug` (accepts legacy uppercase /
    underscore subscription URLs while still blocking traversal).
    Writes use :func:`is_valid_slug` (strict canonical regex) so a
    typo'd slug fails at 400 instead of making it to storage. Public
    ``/<slug>`` RSS and ``/episodes/<slug>/...`` routes are registered
    at the app level and handled by the storage-layer slug guard instead.
    """
    if not values or 'slug' not in values:
        return
    from utils.validation import is_valid_slug, is_dangerous_slug
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
        client_ip = request.headers.get('X-Forwarded-For', request.remote_addr)
        user_agent = request.headers.get('User-Agent', 'Unknown')[:100]

        try:
            result = f(*args, **kwargs)
            elapsed = (time.time() - start_time) * 1000  # ms
            status = result.status_code if hasattr(result, 'status_code') else 200
            logger.info(f"{request.method} {request.path} {status} {elapsed:.0f}ms [{client_ip}] [{user_agent}]")
            return result
        except Exception as e:
            elapsed = (time.time() - start_time) * 1000
            logger.error(f"{request.method} {request.path} ERROR {elapsed:.0f}ms [{client_ip}] - {e}")
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


# Import all sub-modules to trigger route registration
from api import feeds, episodes, history, settings, system, patterns, sponsors, status, auth, search, podcast_search, stats, providers
