"""Session cookie configuration helpers.

Standalone module with no Flask or app-level imports so it can be unit-tested
without triggering the full application initialization sequence.
"""
import os


def base_url_is_plaintext_http(base_url: str) -> bool:
    """Return True when base_url uses plain HTTP (starts with 'http://')."""
    return base_url.lower().startswith('http://')


def base_url_is_https(base_url: str) -> bool:
    """Return True when base_url uses HTTPS (starts with 'https')."""
    return base_url.lower().startswith('https')


def _default_session_cookie_secure() -> bool:
    """Return the value to use for SESSION_COOKIE_SECURE.

    Secure by default. Downgrades to False only when BASE_URL is explicitly
    plain HTTP (starts with 'http://'). Operators serving over plain HTTP must
    set BASE_URL=http://... or set SESSION_COOKIE_SECURE=false explicitly.
    Operators terminating TLS at a proxy must set BASE_URL=https://... (already
    required for correct feed URLs) or set SESSION_COOKIE_SECURE=true explicitly.
    Unset or empty BASE_URL defaults to True (safe).
    """
    explicit = os.environ.get('SESSION_COOKIE_SECURE')
    if explicit is not None and explicit.strip().lower() != 'auto':
        return explicit.strip().lower() == 'true'
    base_url = os.environ.get('BASE_URL', '')
    # Downgrade only on a positive plain-HTTP signal; unknown/empty stays True.
    return not base_url_is_plaintext_http(base_url)
