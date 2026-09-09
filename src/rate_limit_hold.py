"""Rate-limit queue hold (#696): pause the queue until a 429 reset passes.

A held 429 sends its episode back to pending and stamps HOLD_UNTIL_KEY; no
processing starts until that time, then the queue processor clears the
marker on its next pass. Episodes never leave the normal queue.

While a hold is active, probe_rate_limit() periodically re-checks it: an
operator-configured usage URL (tier 1) or a minimal LLM completion (tier 2)
can clear the hold early or re-stamp it with a fresher reset, since the
429's own stated reset can be wrong in either direction.
"""
import logging
from datetime import timedelta

from config import coerce_bool_setting
from database.settings import registry_current_value, registry_default
from llm_client import extract_retry_after, get_llm_client, is_rate_limit_error
from utils.safe_http import safe_get, URLTrust
from utils.time import ISO_FORMAT, epoch_to_iso, parse_iso_utc, utc_now, utc_now_iso
from webhook_service import fire_queue_held_event, fire_queue_resumed_event

logger = logging.getLogger('podcast.refresh')

HOLD_UNTIL_KEY = 'rate_limit_hold_until'
HOLD_SINCE_KEY = 'rate_limit_hold_since'
RATE_LIMIT_PROBE_AT_KEY = 'rate_limit_probe_at'

# A provider reset farther out than this is treated as unusable reset info;
# 24h covers the common per-minute and per-day windows.
MAX_RESET_SECONDS = 24 * 3600
# Ceiling on a usage-derived hold: rejects absurd payloads (milliseconds sent
# as seconds) while allowing real weekly and monthly provider windows.
MAX_HOLD_SECONDS = 30 * 24 * 3600
# Below this, the in-process sleep-retry still handles it, so a lone
# throttled window recovers without pausing the queue.
MIN_HOLD_RESET_SECONDS = 300
# Short: a hold probe running unusually long should not itself stall the
# dispatcher, which calls it inline before its own 30s wait.
PROBE_TIMEOUT_SECONDS = 8
RATE_LIMIT_PROBE_MINUTES_MIN = 0
RATE_LIMIT_PROBE_MINUTES_MAX = 60

# hold_until values already warned about for a failed tier-1 probe, so a
# repeatedly-failing usage URL logs once per hold instead of once per probe.
_warned_probe_holds: set[str] = set()


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


def record_hold_until(db, retry_at_iso: str, *, force: bool = False) -> tuple[str, bool]:
    """Stamp the pause marker, keeping whichever reset is later so a second
    429 can extend an active pause but never cut it short. Returns the
    effective hold_until and whether this call started a new pause (as
    opposed to extending or falling inside an active one).

    force=True (rate-limit probe re-stamps) skips the "later wins" guard:
    the probe's fresher provider read is authoritative and may pull the
    release closer as well as push it out.
    """
    current = get_hold_until(db)
    if not force and current and parse_iso_utc(current) and parse_iso_utc(current) > parse_iso_utc(retry_at_iso):
        return current, False
    # Extending an active pause keeps its start; only a fresh pause stamps it.
    started = not hold_is_active(current)
    if started:
        db.set_setting(HOLD_SINCE_KEY, utc_now_iso())
    db.set_setting(HOLD_UNTIL_KEY, retry_at_iso)
    return retry_at_iso, started


def clear_hold(db) -> str | None:
    """Drop the pause marker, its start stamp, and the probe cadence stamp;
    returns when the hold began."""
    held_since = db.get_setting(HOLD_SINCE_KEY)
    db.clear_setting(HOLD_UNTIL_KEY)
    db.clear_setting(HOLD_SINCE_KEY)
    db.clear_setting(RATE_LIMIT_PROBE_AT_KEY)
    return held_since


def clear_hold_if_unchanged(db, hold_until: str) -> tuple[bool, str | None]:
    """Drop the pause only while the marker still reads `hold_until`.

    A 429 landing between a caller's read and this call owns a newer marker,
    and resuming on it would put the queue straight back into the limit.
    """
    held_since = db.get_setting(HOLD_SINCE_KEY)
    if not db.clear_setting_if_equal(HOLD_UNTIL_KEY, hold_until):
        return False, None
    db.clear_setting(HOLD_SINCE_KEY)
    db.clear_setting(RATE_LIMIT_PROBE_AT_KEY)
    return True, held_since


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


def hold_message(hold_until: str | None, error) -> str:
    """error_message written on an episode a 429 sent back to the queue.

    hold_until is None when the hold feature is off: nothing was paused.
    """
    if not hold_until:
        return f"LLM rate limit: {error}"
    return f"Paused (LLM rate limit until {hold_until}): {error}"


def rate_limit_hold_tick(db) -> None:
    """Clear a pause marker whose reset time has passed."""
    hold_until = get_hold_until(db)
    if not hold_until or hold_is_active(hold_until):
        return
    cleared, held_since = clear_hold_if_unchanged(db, hold_until)
    if not cleared:
        return
    logger.info("Rate-limit hold: queue pause lifted after provider reset")
    fire_queue_resumed_event(held_since=held_since)


def get_llm_usage_url(db) -> str:
    """Operator-configured provider usage/limit endpoint; '' when unset."""
    return registry_current_value(db, 'llm_usage_url') or ''


def get_rate_limit_probe_minutes(db) -> int:
    """Probe cadence in minutes; 0 disables probing. Default 5."""
    try:
        return int(registry_current_value(db, 'rate_limit_probe_minutes'))
    except (TypeError, ValueError):
        return int(registry_default('rate_limit_probe_minutes'))


def read_usage_status(usage_url: str) -> dict | None:
    """GET `usage_url` and return its parsed JSON object; None on any
    transport, HTTP-status, or non-object-JSON failure."""
    try:
        response = safe_get(usage_url, trust=URLTrust.OPERATOR_CONFIGURED,
                            timeout=PROBE_TIMEOUT_SECONDS)
        response.raise_for_status()
        payload = response.json()
    except Exception as e:
        logger.debug(f"Rate-limit probe: usage URL read failed: {e}")
        return None
    return payload if isinstance(payload, dict) else None


def _capped_reset_iso(reset_at) -> str:
    """Reset time capped at MAX_HOLD_SECONDS out, so a bad payload cannot
    pause the queue for years."""
    return min(reset_at, utc_now() + timedelta(seconds=MAX_HOLD_SECONDS)).strftime(ISO_FORMAT)


def usage_reset_iso(payload: dict) -> str | None:
    """Absolute ISO reset time from a `blocked: true` usage payload.

    Prefers seconds_until_reset, then the blocked_until epoch, then
    blocked_until_iso. None when nothing usable is present.
    """
    seconds = payload.get('seconds_until_reset')
    if isinstance(seconds, (int, float)) and not isinstance(seconds, bool):
        return _capped_reset_iso(utc_now() + timedelta(seconds=max(0.0, float(seconds))))
    blocked_until = payload.get('blocked_until')
    if isinstance(blocked_until, (int, float)) and not isinstance(blocked_until, bool):
        parsed = parse_iso_utc(epoch_to_iso(blocked_until))
        if parsed:
            return _capped_reset_iso(parsed)
    blocked_until_iso = payload.get('blocked_until_iso')
    if isinstance(blocked_until_iso, str):
        parsed = parse_iso_utc(blocked_until_iso)
        if parsed:
            return _capped_reset_iso(parsed)
    return None


def hold_queue_for_provider_limit(db, error, *, slug: str, episode_id: str,
                                  podcast_name: str) -> str | None:
    """Pause the queue for a 429 and alert once per pause; returns the effective
    hold_until, or None when the hold feature is off.

    A configured usage endpoint is preferred over the 429's own stated reset,
    which can be wrong in either direction.
    """
    if not is_rate_limit_hold_enabled(db):
        return None
    hold_until_iso = None
    usage_url = get_llm_usage_url(db)
    if usage_url:
        payload = read_usage_status(usage_url)
        if payload is not None and payload.get('blocked') is True:
            hold_until_iso = usage_reset_iso(payload)
    if hold_until_iso is None:
        hold_until_iso = (utc_now() + timedelta(
            seconds=max(0.0, float(error.retry_after_seconds)))).strftime(ISO_FORMAT)
    hold_until, started = record_hold_until(db, hold_until_iso)
    logger.warning(f"[{slug}:{episode_id}] Rate-limit hold: paused until "
                   f"{hold_until} (provider reset)")
    # One alert per pause: a later 429 under it only moves the reset out.
    if started:
        fire_queue_held_event(hold_until=hold_until, error_message=error, slug=slug,
                              episode_id=episode_id, podcast_name=podcast_name)
    return hold_until


def _warn_probe_failure(hold_until: str, message: str) -> None:
    """WARNING once per hold_until (repeat failures log at DEBUG)."""
    if hold_until in _warned_probe_holds:
        logger.debug(message)
        return
    _warned_probe_holds.add(hold_until)
    logger.warning(message)


def _probe_usage_url(db, usage_url: str, hold_until: str) -> bool | None:
    """Tier 1: check the operator's usage endpoint.

    Returns True when the hold was cleared or re-stamped from this
    response, None when the response was unusable so tier 2 should run.
    """
    payload = read_usage_status(usage_url)
    if payload is None:
        _warn_probe_failure(
            hold_until, f"Rate-limit probe: usage URL unreachable or invalid ({usage_url})")
        return None
    blocked = payload.get('blocked')
    if blocked is False:
        held_since = clear_hold(db)
        fire_queue_resumed_event(held_since=held_since)
        logger.info("Rate-limit probe: usage endpoint reports clear; resuming queue")
        return True
    if blocked is True:
        reset_iso = usage_reset_iso(payload)
        if reset_iso is not None:
            record_hold_until(db, reset_iso, force=True)
            logger.info(f"Rate-limit probe: usage endpoint re-stamped hold to {reset_iso}")
            return True
        _warn_probe_failure(
            hold_until, f"Rate-limit probe: usage URL blocked with no usable reset ({usage_url})")
        return None
    _warn_probe_failure(
        hold_until, f"Rate-limit probe: usage URL payload missing boolean 'blocked' ({usage_url})")
    return None


def _probe_via_completion(db) -> bool:
    """Tier 2: one minimal completion through the configured LLM client."""
    model = db.get_setting('claude_model')
    if not model:
        return False
    try:
        get_llm_client().messages_create(
            model=model, max_tokens=1, system='',
            messages=[{"role": "user", "content": "hi"}],
            timeout=PROBE_TIMEOUT_SECONDS,
        )
    except Exception as e:
        if is_rate_limit_error(e):
            hold_after = extract_retry_after(e, max_seconds=MAX_RESET_SECONDS)
            if hold_after is not None:
                hold_until_iso = (utc_now() + timedelta(seconds=max(0.0, hold_after))).strftime(ISO_FORMAT)
                record_hold_until(db, hold_until_iso, force=True)
                logger.info(f"Rate-limit probe: completion probe re-stamped hold to {hold_until_iso}")
            return False
        logger.debug(f"Rate-limit probe: completion probe failed, leaving hold: {e}")
        return False
    held_since = clear_hold(db)
    fire_queue_resumed_event(held_since=held_since)
    logger.info("Rate-limit probe: completion probe succeeded; resuming queue")
    return True


def probe_rate_limit(db) -> bool:
    """Probe an active rate-limit hold and clear or re-stamp it; never raises.

    Runs at most once per rate_limit_probe_minutes. See module docstring
    for the tier order (usage URL, then a minimal completion).
    """
    try:
        return _probe_rate_limit(db)
    except Exception:
        logger.exception("Rate-limit probe crashed; leaving hold untouched")
        return False


def _probe_rate_limit(db) -> bool:
    hold_until, _ = get_active_hold(db)
    if not hold_until:
        return False
    minutes = get_rate_limit_probe_minutes(db)
    if minutes <= 0:
        return False
    last_probe_at = parse_iso_utc(db.get_setting(RATE_LIMIT_PROBE_AT_KEY))
    if last_probe_at and (utc_now() - last_probe_at).total_seconds() < minutes * 60:
        return False
    db.set_setting(RATE_LIMIT_PROBE_AT_KEY, utc_now_iso())

    usage_url = get_llm_usage_url(db)
    if usage_url:
        result = _probe_usage_url(db, usage_url, hold_until)
        if result is not None:
            return result

    return _probe_via_completion(db)
