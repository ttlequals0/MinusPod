"""Rate-limit queue hold (#696).

When the LLM provider answers 429 with a reset time (Retry-After header or
body hint) and the hold is enabled, the retry loop in utils.llm_call breaks
with a typed ProviderRateLimitedError instead of sleeping in the worker
thread. _handle_processing_failure defers the episode with
deferred_service='llm_rate_limit' and stamps a global hold-until timestamp;
the queue processor pauses new claims until it passes. This tick, run from
the background queue processor's ~5-minute maintenance block beside the
offline-queue tick, releases held episodes once the reset has passed and
expires holds past the TTL.

The toggle gates only NEW holds, mirroring the offline queue: the tick keeps
running for episodes already held even when the toggle is later disabled.
"""
import logging
from datetime import datetime, timezone


# webhook_service is lazy-imported in _notify_expired so the LLM call path
# (utils.llm_call -> this module) keeps it out of its import-time graph.

logger = logging.getLogger('podcast.refresh')

RATE_LIMIT_DEFERRED_SERVICE = 'llm_rate_limit'
HOLD_UNTIL_KEY = 'rate_limit_hold_until'

TTL_HOURS_DEFAULT = 48
TTL_HOURS_MIN = 1
TTL_HOURS_MAX = 720

# A provider reset farther out than this is treated as unusable reset info;
# 24h covers the common per-minute and per-day windows.
MAX_RESET_SECONDS = 24 * 3600


def is_rate_limit_hold_enabled(db) -> bool:
    """Rate-limit hold toggle read from a live db handle; off by default."""
    try:
        return db.get_setting_bool('rate_limit_hold_enabled', default=False)
    except Exception:
        return False


def is_rate_limit_hold_enabled_cached() -> bool:
    """TTL-cached enabled flag for the LLM call hot path (no db handle)."""
    try:
        from llm_client import _get_cached_setting
        return (_get_cached_setting('rate_limit_hold_enabled') or '') == 'true'
    except Exception:
        return False


def get_rate_limit_hold_ttl_hours(db=None) -> int:
    """Configured TTL in hours, clamped to [1, 720]; default 48."""
    if db is None:
        from database import Database
        db = Database()
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
    if current and _parse_iso(current) and _parse_iso(current) > _parse_iso(retry_at_iso):
        return
    db.set_setting(HOLD_UNTIL_KEY, retry_at_iso)


def _parse_iso(value):
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00'))
    except (TypeError, ValueError, AttributeError):
        return None


def is_queue_paused(db) -> bool:
    """True while a recorded hold's reset time is still in the future."""
    hold_until = get_hold_until(db)
    if not hold_until:
        return False
    reset_at = _parse_iso(hold_until)
    return bool(reset_at and reset_at > datetime.now(timezone.utc))


def hold_reset_seconds(error) -> float | None:
    """Reset delay (seconds) to hold a queue pause on, or None when the
    hold is disabled or the provider gave no usable reset time."""
    if not is_rate_limit_hold_enabled_cached():
        return None
    try:
        from llm_client import extract_retry_after
        return extract_retry_after(error, max_seconds=MAX_RESET_SECONDS)
    except Exception:
        return None


def _notify_expired(db, expired) -> None:
    """History + webhook for TTL-expired holds, matching the offline queue's
    permanent-failure audit trail."""
    from webhook_service import fire_event, EVENT_EPISODE_FAILED
    for episode in expired:
        try:
            db.record_processing_history(
                podcast_id=episode['podcast_id'],
                podcast_slug=episode['podcast_slug'],
                podcast_title=episode.get('podcast_title'),
                episode_id=episode['episode_id'],
                episode_title=episode.get('title'),
                status='failed',
                error_message=episode.get('error_message'),
            )
        except Exception as hist_err:
            logger.warning(
                f"Rate-limit hold: history record failed for "
                f"{episode['podcast_slug']}:{episode['episode_id']}: {hist_err}")
        try:
            fire_event(
                event=EVENT_EPISODE_FAILED,
                episode_id=episode['episode_id'],
                slug=episode['podcast_slug'],
                episode_title=episode.get('title'),
                processing_time=0.0,
                llm_cost=0.0,
                error_message=episode.get('error_message'),
                podcast_name=episode.get('podcast_title'),
            )
        except Exception as wh_err:
            logger.warning(
                f"Rate-limit hold: webhook fire failed for "
                f"{episode['podcast_slug']}:{episode['episode_id']}: {wh_err}")


def rate_limit_hold_tick(db) -> None:
    """One maintenance pass: release held episodes whose reset time passed,
    expire holds whose TTL has run out, clear the pause marker when elapsed.

    Runs for episodes already held even when the toggle is off; the toggle
    gates only new holds.
    """
    held = [e for e in db.get_deferred_episodes()
            if (e.get('deferred_service') or '') == RATE_LIMIT_DEFERRED_SERVICE]
    if not held and not is_queue_paused(db):
        return

    expired = db.expire_rate_limit_holds(get_rate_limit_hold_ttl_hours(db))
    _notify_expired(db, expired)

    if is_queue_paused(db):
        return

    requeued = db.requeue_rate_limit_holds()
    if requeued:
        logger.info(f"Rate-limit hold: released {requeued} held episodes after provider reset")

    if get_hold_until(db):
        # Reset time passed; clear the stale marker so the claim gate unblocks.
        db.clear_setting(HOLD_UNTIL_KEY)
        logger.info("Rate-limit hold: queue pause lifted after provider reset")
