"""Global feed-key auth for the public feed surface (authenticated feeds).

When the ``feed_auth_enabled`` setting is on, every public feed/asset route
(RSS, episode mp3, transcript vtt, chapters.json, badged cover art) requires
the global feed key. RSS and episode assets carry it as a ``?key=`` query
param; cover art embeds it in the path token (``cover-minuspod-<version>-
<key>.jpg``) because podcast apps reject image URLs that do not end in a real
image extension (proven with Pocket Casts in 2.32.5).

The key is 64 lowercase hex chars (``secrets.token_hex(32)``, the
flask_secret_key precedent) - hex has no hyphens, so the cover token splits
unambiguously. It is stored plaintext in settings (``feed_auth_key``): it must
be readable back for the UI/API, and secrets_crypto may be locked. Validation
reads the DB per request (no caching) so a rotation applies instantly across
all workers. The admin surface (/api, /ui) is not gated by this module.
"""
import logging
import re
import secrets
from functools import wraps

from flask import abort, request

from utils.http import client_ip

logger = logging.getLogger('podcast.feed')

KEY_RE = re.compile(r'[0-9a-f]{64}')


def generate_feed_key() -> str:
    """64 lowercase hex chars; charset matters for cover-token parsing."""
    return secrets.token_hex(32)


def feed_auth_enabled(db) -> bool:
    return db.get_setting_bool('feed_auth_enabled', False)


def active_feed_key(db):
    """The enforced key, or None when auth is disabled or no key is stored.

    Callers emitting URLs use this so keyless serving resumes the moment the
    feature is disabled, even though the stored key is retained for re-enable.
    """
    if not feed_auth_enabled(db):
        return None
    return db.get_setting('feed_auth_key') or None


def extract_key_from_cover_token(token):
    """Pull the feed key out of a cover-art path token.

    The token is ``<version>-<key>``, ``<key>`` alone, or a keyless
    ``<version>``; version is 8 hex chars and the key 64, so the last
    hyphen-separated segment either fullmatches KEY_RE or there is no key.
    """
    if not token:
        return None
    candidate = token.rsplit('-', 1)[-1]
    return candidate if KEY_RE.fullmatch(candidate) else None


def require_feed_key(f):
    """Route decorator: 401 unless the request carries the active feed key.

    No-op while feed auth is disabled. Fails closed when enabled but no key
    is stored. Reads the key from ``?key=`` or, for cover art, the path token
    kwarg. HEAD is covered automatically (Flask serves HEAD via the GET view).
    """

    @wraps(f)
    def wrapper(*args, **kwargs):
        from main_app import db  # lazy: avoid import cycle at module load

        if feed_auth_enabled(db):
            expected = db.get_setting('feed_auth_key')
            supplied = (request.args.get('key')
                        or extract_key_from_cover_token(kwargs.get('token')))
            # KEY_RE prefilter: compare_digest raises TypeError on non-ASCII
            # input, which would turn a garbage ?key= into a 500 instead of
            # the intended 401. Anything non-64-hex can never match anyway.
            if not (expected and supplied and KEY_RE.fullmatch(supplied)
                    and secrets.compare_digest(supplied, expected)):
                logger.warning(
                    f"{request.method} {request.path} 401 no auth key "
                    f"provided or is invalid [{client_ip()}]")
                abort(401)
        return f(*args, **kwargs)

    return wrapper
