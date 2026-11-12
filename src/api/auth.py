"""Authentication routes: /auth/* endpoints."""
import logging
import os

from flask import current_app, request
from werkzeug.security import generate_password_hash, check_password_hash

from utils.session_defaults import base_url_is_https
from utils.time import parse_iso_utc, utc_now
from utils.validation import is_public_ip_for_lockout
from api.auth_state import authenticate_session, current_auth, sign_out
from api.csrf import validate as csrf_validate

from api import (
    api, limiter, log_request, json_response, error_response,
    get_database, retry_after_ceil, retry_after_response,
)

logger = logging.getLogger('podcast.api')


def _session_cookie_will_be_dropped(secure_cookie, request_is_secure, base_url):
    """True when a just-set session cookie won't survive in the browser: it is
    marked Secure but the request arrived over plain HTTP, so the browser
    discards it and the next request looks logged out. Suppressed when BASE_URL
    is https, which means TLS is terminated upstream and the request only looks
    insecure because the proxy hop count is unset (warned about separately)."""
    if not secure_cookie or request_is_secure:
        return False
    return not base_url_is_https(base_url)


def _warn_if_session_cookie_unstorable():
    """Log how to fix the silent login-bounce loop a Secure cookie causes over
    plain HTTP. Called right after authenticating so the warning lands next to
    the successful-login line in the operator's logs."""
    if _session_cookie_will_be_dropped(
        current_app.config.get('SESSION_COOKIE_SECURE', True),
        request.is_secure,
        os.environ.get('BASE_URL', ''),
    ):
        logger.warning(
            "Login authenticated over plain HTTP while SESSION_COOKIE_SECURE is "
            "enabled; the browser will discard the session cookie and bounce the "
            "user back to the login screen. Set SESSION_COOKIE_SECURE=false to "
            "serve over plain HTTP, or put the UI behind HTTPS."
        )


def _retry_after_seconds(locked_until_iso: str) -> int:
    unlock = parse_iso_utc(locked_until_iso)
    return retry_after_ceil(None if unlock is None else (unlock - utc_now()).total_seconds())


# ========== Authentication Endpoints ==========

@api.route('/auth/status', methods=['GET'])
@log_request
def auth_status():
    """Check authentication status.

    Returns whether password is set and if current session is authenticated.
    This endpoint is always accessible (no auth required).
    """
    auth = current_auth()
    return json_response({
        'passwordSet': auth.password_set,
        'authenticated': auth.authenticated
    })


@api.route('/auth/login', methods=['POST'])
@limiter.limit("3 per minute")
@limiter.limit("10 per hour")
@log_request
def auth_login():
    """Login with password.

    Request body:
    {
        "password": "your-password",
        "rememberMe": false   // optional boolean: longer session on this device
    }
    """
    db = get_database()
    stored_hash = db.get_setting('app_password')
    password_set = stored_hash is not None and stored_hash != ''

    if not password_set:
        if current_auth().required:
            return error_response('Set the application password before logging in', 503)
        return json_response({
            'authenticated': True,
            'message': 'No password configured'
        })

    ip = request.remote_addr or ''

    # Lockout only fires on public IPs so operators behind RFC1918, CGNAT,
    # Docker bridges, or Tailscale ULA prefixes are not denied by an
    # attacker who shares their NAT. Flask-limiter still rate-limits
    # everyone via the @limiter.limit decorators above.
    if is_public_ip_for_lockout(ip):
        locked_until = db.check_lockout(ip)
        if locked_until:
            logger.warning("Login attempt on locked IP %s (until %s)", ip, locked_until)
            seconds = _retry_after_seconds(locked_until)
            return retry_after_response(
                'Too many failed attempts; try again later', seconds, 429, in_body=True)

    data = request.get_json(silent=True)
    if not isinstance(data, dict) or not isinstance(data.get('password'), str):
        return error_response('Password is required', 400)

    remember = data.get('rememberMe', False)
    if not isinstance(remember, bool):
        return error_response('rememberMe must be a boolean', 400)

    password = data['password']

    if not stored_hash or not check_password_hash(stored_hash, password):
        logger.warning(f"Failed login attempt from {ip}")
        if is_public_ip_for_lockout(ip):
            db.record_auth_failure(ip)
        return error_response('Invalid password', 401)

    if is_public_ip_for_lockout(ip):
        db.record_auth_success(ip)

    # Rotate the session on login so a pre-auth cookie can't ride the new authenticated state (fixation).
    generation = db.auth_generation_if_password_equal(stored_hash)
    if generation is None:
        return error_response('Password changed during login; try again', 409)
    authenticate_session(db, generation, remembered=remember)
    logger.info(f"Successful login from {ip}")
    _warn_if_session_cookie_unstorable()

    return json_response({
        'authenticated': True,
        'message': 'Login successful'
    })


@api.route('/auth/logout', methods=['POST'])
@log_request
def auth_logout():
    """Logout and clear session.

    Listed in AUTH_EXEMPT_PATHS so an expired session can still call it,
    which means the blueprint-level CSRF check in check_auth() is skipped.
    Enforce it manually here so a cross-site POST cannot terminate an
    authenticated session. A caller without a live sign-in skips the token
    check, so it can always clear stale state.
    """
    csrf_err = csrf_validate(request)
    if csrf_err:
        logger.warning("CSRF check failed on /auth/logout ip=%s", request.remote_addr)
        return error_response(csrf_err, 403)

    sign_out(get_database())
    logger.info(f"Logout from {request.remote_addr}")

    return json_response({
        'authenticated': False,
        'message': 'Logged out successfully'
    })


@api.route('/auth/password', methods=['PUT'])
@limiter.limit("3 per hour")
@log_request
def auth_set_password():
    """Set or change the application password.

    If no password is currently set, this creates a new password.
    If a password is set, the current password must be provided.

    Request body:
    {
        "currentPassword": "old-password",  // Required if password is set
        "newPassword": "new-password"       // Min 12 characters
    }

    To remove password protection, set newPassword to empty string or null.
    """
    data = request.get_json()
    if not data:
        return error_response('Request body required', 400)

    db = get_database()
    current_hash = db.get_setting('app_password')
    password_set = current_hash is not None and current_hash != ''

    # If password is set, verify current password
    if password_set:
        current_password = data.get('currentPassword', '')
        if not check_password_hash(current_hash, current_password):
            logger.warning(f"Failed password change attempt from {request.remote_addr}")
            return error_response('Current password is incorrect', 401)

    new_password = data.get('newPassword', '')

    # Remove password protection if empty
    if not new_password:
        if db.replace_password_and_revoke(current_hash, '') is None:
            return error_response('Password changed; try again', 409)
        sign_out(db)
        logger.info(f"Password protection removed by {request.remote_addr}")
        return json_response({
            'message': 'Password protection removed',
            'passwordSet': False
        })

    # Validate new password (grandfathered: pre-existing hashes with shorter
    # passwords still verify cleanly; the new minimum only applies to the
    # set/change path).
    if len(new_password) < 12:
        return error_response('Password must be at least 12 characters', 400)

    # Pin the hash method so security decisions are visible in code rather
    # than depending on whichever default werkzeug ships today.
    password_hash = generate_password_hash(new_password, method='scrypt')
    remembered = current_auth().remembered
    generation = db.replace_password_and_revoke(current_hash, password_hash)
    if generation is None:
        return error_response('Password changed; try again', 409)
    logger.info(f"Password {'changed' if password_set else 'set'} by {request.remote_addr}")

    # Rotate the session on password change for the same reason as /auth/login.
    authenticate_session(db, generation, remembered=remembered)
    _warn_if_session_cookie_unstorable()

    return json_response({
        'message': f"Password {'changed' if password_set else 'set'} successfully",
        'passwordSet': True
    })
