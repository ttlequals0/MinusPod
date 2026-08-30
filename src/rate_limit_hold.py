"""Rate-limit queue hold (#696).

When the LLM provider answers 429 with a reset time (Retry-After header or
body hint) and the hold is enabled, the retry loop in utils.llm_call breaks
with a typed ProviderRateLimitedError instead of sleeping in the worker
thread. _handle_processing_failure defers the episode with
deferred_service='llm_rate_limit' and stamps a global hold-until timestamp;
the queue processor pauses new claims until it passes. This tick, run from
the background queue processor's ~5-minute maintenance block beside the
offline-queue tick, releases held episodes once the reset has passed and
expires holds past the TTL; the deferred-row lifecycle (expiry, requeue,
failure notifications) is shared with the offline queue.

While the toggle stays on, the tick keeps running for episodes already
held even across restarts. Turning the toggle off is the operator escape
hatch: the settings handler clears the pause marker, and this tick then
releases every held episode on its next pass.
"""
import logging

from utils.time import parse_iso_utc, utc_now

# offline_queue's TTL constants and expiry notifier are lazy-imported where
# used: a module-level import would drag transcriber + webhook_service into
# the LLM call path's import-time graph, which utils.llm_call deliberately
# avoids.

logger = logging.getLogger('podcast.refresh')

RATE_LIMIT_DEFERRED_SERVICE = 'llm_rate_limit'
HOLD_UNTIL_KEY = 'rate_limit_hold_until'

# A provider reset farther out than this is treated as unusable reset info;
# 24h covers the common per-minute and per-day windows.
MAX_RESET_SECONDS = 24 * 3600
# Resets shorter than this keep the existing in-process sleep-retry, so a
# lone throttled window still recovers instead of failing its episode into
# the hold; only real backoff windows (past the old retry-after sleep cap)
# pause the queue.
MIN_HOLD_RESET_SECONDS = 300


def is_rate_limit_hold_enabled(db=None) -> bool:
    """Rate-limit hold toggle; off by default.

    With a db handle (failure-handler path) reads directly; without one (LLM
    call hot path) reads through llm_client's short-TTL settings cache.
    """
    try:
        if db is not None:
            return db.get_setting_bool('rate_limit_hold_enabled', default=False)
        from llm_client import _get_cached_setting
        return (_get_cached_setting('rate_limit_hold_enabled') or '') == 'true'
    except Exception:
        return False


def get_rate_limit_hold_ttl_hours(db) -> int:
    """Configured TTL in hours, clamped to [1, 720]; default 48."""
    from offline_queue import TTL_HOURS_DEFAULT, TTL_HOURS_MAX, TTL_HOURS_MIN
    try:
        ttl = int(db.get_setting('rate_limit_hold_ttl_hours') or TTL_HOURS_DEFAULT)
    except (TypeError, ValueError):
        ttl = TTL_HOURS_DEFAULT
    return max(TTL_HOURS_MIN, min(ttl, TTL_HOURS_MAX))


def get_hold_until(db) -> str | None:
    """ISO timestamp until which new queue claims pause, or None."""
    try:
        return db.get_setting(HOLD_UNTIL_KEY) or None
    except Exception:
        return None


def record_hold_until(db, retry_at_iso: str) -> None:
    """Stamp the pause marker, keeping the longest reset seen so a second
    429 with a later reset cannot shorten an active pause."""
    current = get_hold_until(db)
    if current and parse_iso_utc(current) and parse_iso_utc(current) > parse_iso_utc(retry_at_iso):
        return
    db.set_setting(HOLD_UNTIL_KEY, retry_at_iso)


def _is_paused(hold_until: str | None) -> bool:
    return bool(hold_until and parse_iso_utc(hold_until)
                and parse_iso_utc(hold_until) > utc_now())


def is_queue_paused(db) -> bool:
    """True while a recorded hold's reset time is still in the future."""
    return _is_paused(get_hold_until(db))


def rate_limit_hold_tick(db) -> None:
    """One maintenance pass: release held episodes whose reset time passed,
    expire holds whose TTL has run out, clear the pause marker when elapsed.

    Runs for episodes already held even when the toggle is off; the toggle
    gates only new holds.
    """
    hold_until = get_hold_until(db)
    held_count = db.count_deferred_episodes(service=RATE_LIMIT_DEFERRED_SERVICE)
    if not held_count and not _is_paused(hold_until):
        return

    expired = db.expire_deferred_episodes(
        get_rate_limit_hold_ttl_hours(db), service=RATE_LIMIT_DEFERRED_SERVICE)
    from offline_queue import notify_expired_episodes
    notify_expired_episodes(db, expired, label='Rate-limit hold')

    # A failure may have recorded a newer hold while the expiry ran; its
    # pause must outlive this pass.
    if _is_paused(get_hold_until(db)):
        return

    requeued = db.requeue_deferred_episodes({RATE_LIMIT_DEFERRED_SERVICE})
    if requeued:
        logger.info(f"Rate-limit hold: released {requeued} held episodes after provider reset")

    if hold_until and get_hold_until(db) == hold_until:
        # Reset time passed and nothing re-stamped it; clear the stale
        # marker so the claim gate unblocks.
        db.clear_setting(HOLD_UNTIL_KEY)
        logger.info("Rate-limit hold: queue pause lifted after provider reset")
