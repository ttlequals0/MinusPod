"""Authentication routes: /auth/* endpoints."""
import logging

from flask import request, session
from werkzeug.security import generate_password_hash, check_password_hash

from utils.validation import is_public_ip_for_lockout

from api import (
    api, limiter, log_request, json_response, error_response,
    get_database,
)

logger = logging.getLogger('podcast.api')


# ========== Authentication Endpoints ==========

@api.route('/auth/status', methods=['GET'])
@log_request
def auth_status():
    """Check authentication status.

    Returns whether password is set and if current session is authenticated.
    This endpoint is always accessible (no auth required).
    """
    db = get_database()
    password_hash = db.get_setting('app_password')
    password_set = password_hash is not None and password_hash != ''

    # If no password is set, everyone is authenticated
    if not password_set:
        authenticated = True
    else:
        authenticated = session.get('authenticated', False)

    return json_response({
        'passwordSet': password_set,
        'authenticated': authenticated
    })


@api.route('/auth/login', methods=['POST'])
@limiter.limit("3 per minute")
@limiter.limit("10 per hour")
@log_request
def auth_login():
    """Login with password.

    Request body:
    {
        "password": "your-password"
    }
    """
    db = get_database()
    stored_hash = db.get_setting('app_password')
    password_set = stored_hash is not None and stored_hash != ''

    if not password_set:
        return json_response({
            'authenticated': True,
            'message': 'No password configured'
        })

    data = request.get_json()
    if not data or 'password' not in data:
        return error_response('Password is required', 400)

    password = data['password']
    ip = request.remote_addr or ''

    # Lockout only fires on public IPs so operators behind RFC1918, CGNAT,
    # Docker bridges, or Tailscale ULA prefixes are not denied by an
    # attacker who shares their NAT. Flask-limiter still rate-limits
    # everyone via the @limiter.limit decorators above.
    if is_public_ip_for_lockout(ip):
        locked_until = db.check_lockout(ip)
        if locked_until:
            logger.warning("Login attempt on locked IP %s (until %s)", ip, locked_until)
            response = error_response('Too many failed attempts; try again later', 429)
            response.headers['Retry-After'] = locked_until
            return response

    if not stored_hash or not check_password_hash(stored_hash, password):
        logger.warning(f"Failed login attempt from {ip}")
        if is_public_ip_for_lockout(ip):
            db.record_auth_failure(ip)
        return error_response('Invalid password', 401)

    if is_public_ip_for_lockout(ip):
        db.record_auth_success(ip)

    # Set session
    session.permanent = True
    session['authenticated'] = True
    logger.info(f"Successful login from {ip}")

    return json_response({
        'authenticated': True,
        'message': 'Login successful'
    })


@api.route('/auth/logout', methods=['POST'])
@log_request
def auth_logout():
    """Logout and clear session."""
    session.clear()
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
        "newPassword": "new-password"       // Min 8 characters
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
        db.set_setting('app_password', '')
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
    db.set_setting('app_password', password_hash)
    logger.info(f"Password {'changed' if password_set else 'set'} by {request.remote_addr}")

    # Ensure current session is authenticated
    session.permanent = True
    session['authenticated'] = True

    return json_response({
        'message': f"Password {'changed' if password_set else 'set'} successfully",
        'passwordSet': True
    })
