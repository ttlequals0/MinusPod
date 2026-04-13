"""REST API for MinusPod web UI."""
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from typing import Optional
from flask import Blueprint, jsonify, request, Response, session
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash

from config import normalize_model_key
from utils.time import parse_timestamp
from utils.text import extract_text_in_range
from utils.url import validate_url, SSRFError
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
    but the difference is negligible (milliseconds).
    """
    start_time = time.time()
    try:
        from status_service import StatusService
        svc = StatusService()
        svc.set_server_start_time(start_time)
    except Exception:
        pass
    return start_time

_start_time = _init_server_start_time()

api = Blueprint('api', __name__, url_prefix='/api/v1')

# Rate limiter - will be initialized when blueprint is registered with app
# Default limits: 200 requests per minute, 1000 per hour
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["200 per minute", "1000 per hour"],
    storage_uri="memory://",
)


def init_limiter(app):
    """Initialize rate limiter with Flask app."""
    limiter.init_app(app)
    logger.info("Rate limiter initialized: 200/min, 1000/hr default limits")


# Paths that don't require authentication
AUTH_EXEMPT_PATHS = {
    '/api/v1/health',
    '/api/v1/auth/status',
    '/api/v1/auth/login',
    '/api/v1/auth/logout',
}

# Path prefixes that don't require authentication
AUTH_EXEMPT_PREFIXES = (
    '/api/v1/auth/',
    '/api/v1/status/stream',  # SSE stream - EventSource can't handle 401 gracefully
)


@api.before_request
def check_auth():
    """Check authentication before each request.

    Exempt paths:
    - /health - health check endpoint
    - /auth/* - authentication endpoints
    - /feeds/<slug>/rss - RSS feed endpoints (for podcast apps)
    - /feeds/<slug>/episodes/<id>/audio - audio files (for podcast apps)
    """
    path = request.path

    # Check exact path exemptions
    if path in AUTH_EXEMPT_PATHS:
        return None

    # Check prefix exemptions
    for prefix in AUTH_EXEMPT_PREFIXES:
        if path.startswith(prefix):
            return None

    # Allow RSS feeds without auth (for podcast apps)
    if path.endswith('/rss'):
        return None

    # Allow audio files without auth (for podcast apps)
    if '/audio' in path and path.startswith('/api/v1/feeds/'):
        return None

    # Allow artwork without auth (img tags don't redirect on 401)
    if '/artwork' in path and path.startswith('/api/v1/feeds/'):
        return None

    # Check if password is set
    db = get_database()
    password_hash = db.get_setting('app_password')
    if not password_hash or password_hash == '':
        return None  # No password set, allow access

    # Check session
    if not session.get('authenticated', False):
        return error_response('Authentication required', 401)

    return None


def get_storage():
    """Get storage instance."""
    from storage import Storage
    return Storage()


def get_database():
    """Get database instance."""
    from database import Database
    return Database()


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
