"""Global and feed-scoped auth for the public feed surface.

When the ``feed_auth_enabled`` setting is on, every public feed/asset route
(RSS, episode mp3, transcript vtt, chapters.json, badged cover art) requires
the global feed key or a revocable subscriber key scoped to that feed. Served
RSS carries the credential in asset query parameters without changing cached
RSS. Cover art keeps a keyless image path and uses the query parameter too.

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

from database.podcasts import is_recents_feed, recents_cutoff
from flask import abort, request

from utils.http import client_ip

logger = logging.getLogger('podcast.feed')

KEY_RE = re.compile(r'[0-9a-f]{64}')
SUBSCRIBER_KEY_RE = re.compile(r'[0-9a-f]{16}\.[0-9a-f]{64}')


def generate_feed_key() -> str:
    """64 lowercase hex chars; charset matters for cover-token parsing."""
    return secrets.token_hex(32)


def ensure_feed_auth_key(db) -> None:
    """Mint the feed auth key when enforcement is enabled without one.

    Enabled-with-no-key fails closed and locks out every client. The UI
    enable path mints lazily, but the env seed (FEED_AUTH_ENABLED=true on a
    fresh deploy) enables enforcement at boot without passing through it,
    so the leader ensures the key on startup. Clears conditional-GET
    validators for the same reason the UI path does: the refresher must not
    304-skip re-rendering feeds with the new auth state.
    """
    if not db.get_setting_bool('feed_auth_enabled', False):
        return
    if db.get_setting('feed_auth_key'):
        return
    db.set_setting('feed_auth_key', generate_feed_key(), is_default=False)
    db.clear_all_podcast_etags()
    logger.info(
        "Feed auth enabled without a key (env seed); generated one -- "
        "retrieve it in Settings > Security")


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


def subscriber_key_allows_asset(db, slug: str, episode_id: str | None,
                                token: str) -> bool:
    """Allow a direct feed key or a Recents key for a current Recents item."""
    if db.verify_feed_subscriber_key(slug, token):
        return True
    if not episode_id or not db.verify_feed_subscriber_key('recents', token):
        return False
    recents = db.get_podcast_by_slug('recents')
    if not is_recents_feed(recents):
        return False
    return db.is_recent_processed_episode(slug, episode_id, recents_cutoff(recents))


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
            global_match = bool(
                expected and supplied and KEY_RE.fullmatch(supplied)
                and secrets.compare_digest(supplied, expected)
            )
            subscriber_match = bool(
                supplied and SUBSCRIBER_KEY_RE.fullmatch(supplied)
                and subscriber_key_allows_asset(
                    db, kwargs.get('slug') or (args[0] if args else None),
                    kwargs.get('episode_id') or (args[1] if len(args) > 1 else None),
                    supplied,
                )
            )
            if not (global_match or subscriber_match):
                # INFO, not WARNING: with feed auth on, every directory crawler
                # and cold podcast client that lacks the key gets one of these,
                # so it is expected traffic rather than an operator problem.
                logger.info(
                    f"{request.method} {request.path} 401 no auth key "
                    f"provided or is invalid [{client_ip()}]")
                abort(401)
        return f(*args, **kwargs)

    return wrapper
