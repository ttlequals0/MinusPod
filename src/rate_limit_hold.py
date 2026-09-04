"""Rate-limit queue hold (#696): pause the queue until a 429 reset passes.

A held 429 defers its episode and stamps HOLD_UNTIL_KEY; the queue
processor blocks new claims until that time, and the tick below releases
or expires held rows. The toggle gates only new holds, so the tick keeps
draining after it is turned off.
"""
import logging

from config import DEFER_SERVICE_RATE_LIMIT, coerce_bool_setting
from utils.time import parse_iso_utc, utc_now, utc_now_iso
from webhook_service import fire_queue_resumed_event

# offline_queue is lazy-imported below: at module level it would drag
# llm_client and transcriber into the LLM call path's import graph,
# which utils.llm_call deliberately avoids.

logger = logging.getLogger('podcast.refresh')

RATE_LIMIT_DEFERRED_SERVICE = DEFER_SERVICE_RATE_LIMIT
HOLD_UNTIL_KEY = 'rate_limit_hold_until'
HOLD_SINCE_KEY = 'rate_limit_hold_since'
HOLD_LABEL = 'Rate-limit hold'

# A provider reset farther out than this is treated as unusable reset info;
# 24h covers the common per-minute and per-day windows.
MAX_RESET_SECONDS = 24 * 3600
# Below this, the in-process sleep-retry still handles it, so a lone
# throttled window recovers without pausing the queue.
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
        return coerce_bool_setting(_get_cached_setting('rate_limit_hold_enabled'))
    except Exception:
        return False


def get_rate_limit_hold_ttl_hours(db) -> int:
    """Configured TTL in hours, clamped to [1, 720]; default 48."""
    from offline_queue import deferral_ttl_hours
    return deferral_ttl_hours(db, 'rate_limit_hold_ttl_hours')


def get_hold_until(db) -> str | None:
    """ISO timestamp until which new queue claims pause, or None."""
    try:
        return db.get_setting(HOLD_UNTIL_KEY) or None
    except Exception:
        return None


def record_hold_until(db, retry_at_iso: str) -> str:
    """Stamp the pause marker, keeping whichever reset is later so a second
    429 can extend an active pause but never cut it short. Returns the
    effective hold_until; equal to `retry_at_iso` when this call set it."""
    current = get_hold_until(db)
    if current and parse_iso_utc(current) and parse_iso_utc(current) > parse_iso_utc(retry_at_iso):
        return current
    # Extending an active pause keeps its start; only a fresh pause stamps it.
    if not hold_is_active(current):
        db.set_setting(HOLD_SINCE_KEY, utc_now_iso())
    db.set_setting(HOLD_UNTIL_KEY, retry_at_iso)
    return retry_at_iso


def clear_hold(db) -> str | None:
    """Drop the pause marker and its start stamp; returns when the hold began."""
    held_since = db.get_setting(HOLD_SINCE_KEY)
    db.clear_setting(HOLD_UNTIL_KEY)
    db.clear_setting(HOLD_SINCE_KEY)
    return held_since


def hold_is_active(hold_until: str | None) -> bool:
    """True when `hold_until` is a reset time still in the future."""
    return bool(hold_until and parse_iso_utc(hold_until)
                and parse_iso_utc(hold_until) > utc_now())


def is_queue_paused(db) -> bool:
    """True while a recorded hold's reset time is still in the future."""
    return hold_is_active(get_hold_until(db))


def should_pause_claims(db) -> bool:
    """True when the queue processor must not claim its next episode.

    A pending row the user asked for overrides the pause, so a Play or
    Reprocess never parks behind a provider backoff window.
    """
    return is_queue_paused(db) and not db.has_user_requested_pending_row()


def rate_limit_hold_tick(db) -> None:
    """One maintenance pass: release held episodes whose reset time passed,
    expire holds whose TTL has run out, clear the pause marker when elapsed.

    Runs for episodes already held even when the toggle is off; the toggle
    gates only new holds.
    """
    hold_until = get_hold_until(db)
    held_count = db.count_deferred_episodes(service=RATE_LIMIT_DEFERRED_SERVICE)
    if not held_count and not hold_is_active(hold_until):
        return

    expired = db.expire_deferred_episodes(
        get_rate_limit_hold_ttl_hours(db), service=RATE_LIMIT_DEFERRED_SERVICE,
        label=HOLD_LABEL)
    from offline_queue import notify_expired_episodes
    notify_expired_episodes(db, expired, label=HOLD_LABEL)

    # A failure may have recorded a newer hold while the expiry ran; its
    # pause must outlive this pass.
    if hold_is_active(get_hold_until(db)):
        return

    requeued = db.requeue_deferred_episodes({RATE_LIMIT_DEFERRED_SERVICE})
    if requeued:
        logger.info(f"Rate-limit hold: released {requeued} held episodes after provider reset")

    held_since = None
    cleared = False
    if hold_until and get_hold_until(db) == hold_until:
        # Reset time passed and nothing re-stamped it; clear the stale
        # marker so the claim gate unblocks.
        held_since = clear_hold(db)
        cleared = True
        logger.info("Rate-limit hold: queue pause lifted after provider reset")
    # The settings-disable path clears the marker itself, so a release with
    # no marker to clear still counts as a resume.
    if requeued or cleared:
        fire_queue_resumed_event(held_since=held_since, requeued=requeued)
