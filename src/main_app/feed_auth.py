"""Global and feed-scoped auth for the public feed surface.

With ``feed_auth_enabled`` on, every public feed/asset route requires the
global feed key or a revocable per-feed subscriber key. Served RSS carries the
credential in asset query parameters without changing cached RSS.

The key is 64 lowercase hex chars (``secrets.token_hex(32)``); hex has no
hyphens, so the cover token splits unambiguously. It is stored plaintext in
settings because the UI/API must read it back and secrets_crypto may be locked.
Validation reads the DB per request so a rotation applies across all workers.
The admin surface (/api, /ui) is not gated here.
"""
import logging
import re
import secrets
from functools import wraps

from database.feed_subscribers import parse_subscriber_token
from database.podcasts import is_recents_feed, recents_cutoff
from feed_build_support import active_feed_key, feed_auth_enabled  # noqa: F401
from flask import abort, g, request

from utils.http import client_ip

logger = logging.getLogger('podcast.feed')

KEY_RE = re.compile(r'[0-9a-f]{64}')
SUBSCRIBER_KEY_RE = re.compile(r'[0-9a-f]{16}\.[0-9a-f]{64}')


def generate_feed_key() -> str:
    """64 lowercase hex chars; charset matters for cover-token parsing."""
    return secrets.token_hex(32)


def ensure_feed_auth_key(db) -> None:
    """Mint the feed auth key when enforcement is enabled without one.

    Enabled-with-no-key fails closed and locks out every client. The env seed
    (FEED_AUTH_ENABLED=true) enables enforcement at boot without the UI mint
    path, so the leader ensures the key on startup. Clears conditional-GET
    validators so the refresher re-renders feeds with the new auth state.
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


def subscriber_key_id_for_asset(db, slug: str, episode_id: str | None,
                                token: str) -> str | None:
    """Key id when the token may fetch the asset: a feed key, or a Recents key for a current Recents item."""
    parsed = parse_subscriber_token(token)
    if parsed is None:
        return None
    allowed = db.verify_feed_subscriber_key(slug, token)
    if not allowed and episode_id and db.verify_feed_subscriber_key('recents', token):
        recents = db.get_podcast_row('recents')
        allowed = (is_recents_feed(recents) and db.is_recent_processed_episode(
            slug, episode_id, recents_cutoff(recents)))
    return parsed[0] if allowed else None


def extract_key_from_cover_token(token):
    """Pull the feed key out of a cover-art path token.

    Token forms: ``<version>-<key>``, ``<key>``, or keyless ``<version>``. The
    version is 8 hex chars and the key 64, so the last segment fullmatches
    KEY_RE or there is no key.
    """
    if not token:
        return None
    candidate = token.rsplit('-', 1)[-1]
    return candidate if KEY_RE.fullmatch(candidate) else None


def require_feed_key(f):
    """Route decorator: 401 unless the request carries the active feed key.

    No-op while feed auth is disabled; fails closed when enabled with no key
    stored. Reads ``?key=`` or, for cover art, the path token kwarg.
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
            subscriber_key_id = (
                supplied and SUBSCRIBER_KEY_RE.fullmatch(supplied)
                and subscriber_key_id_for_asset(
                    db, kwargs.get('slug') or (args[0] if args else None),
                    kwargs.get('episode_id') or (args[1] if len(args) > 1 else None),
                    supplied,
                )
            ) or None
            if subscriber_key_id:
                g.subscriber_key_id = subscriber_key_id
            if not (global_match or subscriber_key_id):
                # INFO, not WARNING: directory crawlers and cold clients
                # without the key hit this, so it is expected traffic.
                logger.info(
                    f"{request.method} {request.path} 401 no auth key "
                    f"provided or is invalid [{client_ip()}]")
                abort(401)
        return f(*args, **kwargs)

    return wrapper
