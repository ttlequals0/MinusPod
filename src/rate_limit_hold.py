"""Rate-limit queue hold (#696): pause the queue until a 429 reset passes.

A held 429 sends its episode back to pending and stamps HOLD_UNTIL_KEY; no
processing starts until that time, then the queue processor clears the
marker on its next pass. Episodes never leave the normal queue.
"""
import logging

from config import coerce_bool_setting
from utils.time import parse_iso_utc, utc_now, utc_now_iso
from webhook_service import fire_queue_resumed_event

logger = logging.getLogger('podcast.refresh')

HOLD_UNTIL_KEY = 'rate_limit_hold_until'
HOLD_SINCE_KEY = 'rate_limit_hold_since'

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


def get_hold_until(db) -> str | None:
    """Raw pause marker, stale or not; readers want get_active_hold."""
    try:
        return db.get_setting(HOLD_UNTIL_KEY) or None
    except Exception:
        return None


def record_hold_until(db, retry_at_iso: str) -> tuple[str, bool]:
    """Stamp the pause marker, keeping whichever reset is later so a second
    429 can extend an active pause but never cut it short. Returns the
    effective hold_until and whether this call started a new pause (as
    opposed to extending or falling inside an active one)."""
    current = get_hold_until(db)
    if current and parse_iso_utc(current) and parse_iso_utc(current) > parse_iso_utc(retry_at_iso):
        return current, False
    # Extending an active pause keeps its start; only a fresh pause stamps it.
    started = not hold_is_active(current)
    if started:
        db.set_setting(HOLD_SINCE_KEY, utc_now_iso())
    db.set_setting(HOLD_UNTIL_KEY, retry_at_iso)
    return retry_at_iso, started


def clear_hold(db) -> str | None:
    """Drop the pause marker and its start stamp; returns when the hold began."""
    held_since = db.get_setting(HOLD_SINCE_KEY)
    db.clear_setting(HOLD_UNTIL_KEY)
    db.clear_setting(HOLD_SINCE_KEY)
    return held_since


def hold_is_active(hold_until: str | None) -> bool:
    """True when `hold_until` is a reset time still in the future."""
    reset_at = parse_iso_utc(hold_until) if hold_until else None
    return bool(reset_at and reset_at > utc_now())


def get_active_hold(db) -> tuple[str | None, str | None]:
    """(hold_until, hold_since) while the pause is active, else (None, None).

    A marker past its reset waits on the processor's next pass to be
    cleared; readers see no hold at all in that gap.
    """
    hold_until = get_hold_until(db)
    if not hold_is_active(hold_until):
        return None, None
    try:
        return hold_until, db.get_setting(HOLD_SINCE_KEY) or None
    except Exception:
        return hold_until, None


def is_queue_paused(db) -> bool:
    """True while a recorded hold's reset time is still in the future."""
    return hold_is_active(get_hold_until(db))


def hold_message(hold_until: str, error) -> str:
    """error_message written on an episode the hold sent back to the queue."""
    return f"Paused (LLM rate limit until {hold_until}): {error}"


def rate_limit_hold_tick(db) -> None:
    """Clear a pause marker whose reset time has passed."""
    hold_until = get_hold_until(db)
    if not hold_until or hold_is_active(hold_until):
        return
    # Compare and clear: a 429 that landed since the read above owns a newer
    # marker, and clearing it would resume the queue straight into the limit.
    if get_hold_until(db) != hold_until:
        return
    held_since = clear_hold(db)
    logger.info("Rate-limit hold: queue pause lifted after provider reset")
    fire_queue_resumed_event(held_since=held_since)
