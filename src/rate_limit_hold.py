"""Rate-limit queue hold (#696): pause the queue until a 429 reset passes.

A held 429 sends its episode back to pending and stamps a hold-until
marker; no processing starts on that provider until that time, then the
queue processor clears the marker on its next pass. Episodes never leave
the normal queue.

Provider-scoped holds: each provider gets its own marker
(``rate_limit_hold_until:<provider>``), so a hold on one provider does not
pause a run whose phases all use a different, healthy provider. The
original unscoped key (``rate_limit_hold_until``) is kept as a global
fallback: it is still read (and still honored) for one release so an
in-flight hold recorded by pre-migration code, or a caller that cannot
resolve which provider hit the limit, still pauses the queue.

Credential-slot scoping: a provider can have two independent accounts, a
primary and an optional secondary, so the marker also carries the
credential slot (``rate_limit_hold_until:<provider>:<slot>``). A 429 on
one slot must not pause the other same-type account. The primary slot
falls back to the pre-slot per-provider marker and the fully-unscoped
legacy marker (both above), so an existing hold is never lost on upgrade;
the secondary slot has no such fallback, since it cannot have generated a
pre-upgrade hold under either legacy key.

While a hold is active, probe_rate_limit() periodically re-checks it: an
operator-configured usage URL (tier 1) or a minimal LLM completion (tier 2)
can clear the hold early or re-stamp it with a fresher reset, since the
429's own stated reset can be wrong in either direction. The probe targets
whichever key (the effective provider's own marker, or the legacy one) is
actually the active source, so a single-provider install's real
provider-scoped hold still gets probed, logged, and resumed exactly as
before. The dispatcher's blanket pause and the tick's cleanup react to any
active hold, legacy or provider-scoped, as a unit.
"""
import logging
from datetime import timedelta

from config import coerce_bool_setting, get_env_backed_int
from database.settings import registry_current_value, registry_default
from llm_client import (
    extract_retry_after, get_effective_provider, get_llm_client,
    is_rate_limit_error,
)
from utils.safe_http import safe_get, URLTrust
from utils.time import ISO_FORMAT, epoch_to_iso, parse_iso_utc, utc_now, utc_now_iso
from webhook_service import fire_queue_held_event, fire_queue_resumed_event

logger = logging.getLogger('podcast.refresh')

HOLD_UNTIL_KEY = 'rate_limit_hold_until'
HOLD_SINCE_KEY = 'rate_limit_hold_since'
# Marks a hold as a manual MinusPod cap (#747), not a real provider 429, so
# the tier-2 completion probe skips it: probing sends a real uncounted request
# that would clear the cap early and burn the very quota the cap protects. A
# manual hold clears only by time, via the tick when its reset passes.
HOLD_MANUAL_KEY = 'rate_limit_hold_manual'
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


def _hold_until_key(provider_key: str | None) -> str:
    return HOLD_UNTIL_KEY if provider_key is None else f'{HOLD_UNTIL_KEY}:{provider_key}'


def _hold_since_key(provider_key: str | None) -> str:
    return HOLD_SINCE_KEY if provider_key is None else f'{HOLD_SINCE_KEY}:{provider_key}'


def _hold_manual_key(provider_key: str | None) -> str:
    return HOLD_MANUAL_KEY if provider_key is None else f'{HOLD_MANUAL_KEY}:{provider_key}'


def _slot_key(provider_key: str, credential_slot: str) -> str:
    """Marker suffix for one (provider, credential_slot) pair."""
    return f'{provider_key}:{credential_slot}'


def _hold_chain(provider_key: str | None, credential_slot: str) -> list[str | None]:
    """Marker suffixes to check/clear for (provider_key, credential_slot),
    most specific first.

    provider_key=None means only the fully-unscoped legacy marker. A real
    provider_key with credential_slot='primary' also falls back to the
    pre-slot per-provider marker and the fully-unscoped legacy one, so an
    existing hold survives the upgrade to slot-scoped keys. 'secondary' has
    no such fallback: it is a new slot that cannot have generated a
    pre-upgrade hold under either legacy key, and falling back would let a
    primary-only hold wrongly pause it.
    """
    if provider_key is None:
        return [None]
    chain: list[str | None] = [_slot_key(provider_key, credential_slot)]
    if credential_slot == 'primary':
        chain += [provider_key, None]
    return chain


def get_hold_until(db, provider_key: str | None = None) -> str | None:
    """Raw pause marker for provider_key, stale or not; readers want
    get_active_hold. provider_key=None reads the legacy unscoped marker."""
    try:
        return db.get_setting(_hold_until_key(provider_key)) or None
    except Exception:
        return None


def record_hold_until(db, provider_key: str | None, retry_at_iso: str,
                      *, credential_slot: str = 'primary',
                      force: bool = False, manual: bool = False) -> tuple[str, bool]:
    """Stamp (provider_key, credential_slot)'s pause marker, keeping
    whichever reset is later so a second 429 can extend an active pause but
    never cut it short. Returns the effective hold_until and whether this
    call started a new pause (as opposed to extending or falling inside an
    active one).

    provider_key=None writes the legacy unscoped marker (a caller that
    could not resolve which provider hit the limit); a real provider_key
    writes that (provider, credential_slot)'s own marker so the pause
    cannot bleed into a different account, healthy or otherwise.
    credential_slot defaults to 'primary' for callers that predate
    secondary-provider routing.

    force=True (rate-limit probe re-stamps) skips the "later wins" guard:
    the probe's fresher provider read is authoritative and may pull the
    release closer as well as push it out.
    """
    key_suffix = _slot_key(provider_key, credential_slot) if provider_key is not None else None
    until_key = _hold_until_key(key_suffix)
    current = db.get_setting(until_key) or None
    if not force and current and parse_iso_utc(current) and parse_iso_utc(current) > parse_iso_utc(retry_at_iso):
        return current, False
    # Extending an active pause keeps its start; only a fresh pause stamps it.
    started = not hold_is_active(current)
    if started:
        db.set_setting(_hold_since_key(key_suffix), utc_now_iso())
    db.set_setting(until_key, retry_at_iso)
    # A real 429 (manual=False) landing on a manual hold's marker clears the
    # flag so the probe can resume: it is now genuine provider throttling.
    if manual:
        db.set_setting(_hold_manual_key(key_suffix), 'true')
    else:
        db.clear_setting(_hold_manual_key(key_suffix))
    return retry_at_iso, started


def clear_hold(db, provider_key: str | None = None) -> str | None:
    """Drop provider_key's pause marker and start stamp, plus the (shared)
    probe cadence stamp; returns when that hold began."""
    since_key = _hold_since_key(provider_key)
    held_since = db.get_setting(since_key)
    db.clear_setting(_hold_until_key(provider_key))
    db.clear_setting(since_key)
    db.clear_setting(_hold_manual_key(provider_key))
    db.clear_setting(RATE_LIMIT_PROBE_AT_KEY)
    return held_since


def _provider_hold_suffixes(db) -> list[str]:
    """provider_key for every stored provider-scoped hold marker, whether
    or not it is still active."""
    try:
        rows = db.get_connection().execute(
            "SELECT key FROM settings WHERE key LIKE ?",
            (f"{HOLD_UNTIL_KEY}:%",),
        ).fetchall()
        return [row['key'][len(HOLD_UNTIL_KEY) + 1:] for row in rows]
    except Exception:
        return []


def get_any_active_hold(db) -> tuple[str | None, str | None]:
    """(hold_until, hold_since) for whichever active hold, the legacy
    marker or any provider-scoped one, resets latest, or (None, None)
    when nothing is held.

    For callers that react to "is anything held" as a single unit without
    resolving a specific provider: the dispatcher's blanket pause/log/probe
    trigger and /status reporting. NOT for admission, which must check the
    run's own required provider(s) via is_queue_paused(db, provider_key) --
    this would incorrectly treat an unrelated provider's hold as blocking.
    """
    candidates = []
    legacy_until, legacy_since = get_active_hold(db)
    if legacy_until:
        candidates.append((legacy_until, legacy_since))
    for provider in _provider_hold_suffixes(db):
        provider_until = get_hold_until(db, provider)
        if hold_is_active(provider_until):
            candidates.append((provider_until, db.get_setting(_hold_since_key(provider)) or None))
    if not candidates:
        return None, None
    return max(candidates, key=lambda pair: parse_iso_utc(pair[0]))


def any_hold_active(db) -> bool:
    """True while the legacy hold or any provider-scoped hold is active.

    For bookkeeping that classifies an episode's pending status without
    knowing which provider its run used, and for the hold-disable escape
    hatch. Not scoped enough to gate a start: admission must check the
    run's own required provider(s) via is_queue_paused(db, provider_key).
    """
    return get_any_active_hold(db)[0] is not None


def clear_all_holds(db) -> str | None:
    """Clear the legacy hold and every provider/slot-scoped hold; returns the
    earliest held_since seen. Used when an operator turns the hold feature
    off entirely, which lifts every account's pause, not just one provider's.

    Checks each stored suffix's own marker directly rather than through
    get_active_hold's fallback chain: these suffixes are already the literal
    stored keys (legacy per-provider or slot-scoped), not a provider_key to
    resolve, so re-running the chain here would be redundant at best and,
    for a composite provider:slot suffix, unreadable.
    """
    held_since = clear_hold(db) if hold_is_active(get_hold_until(db)) else None
    for suffix in _provider_hold_suffixes(db):
        if hold_is_active(get_hold_until(db, suffix)):
            since = clear_hold(db, suffix)
            held_since = held_since or since
    return held_since


def clear_hold_for_provider_change(db, reason: str, *,
                                   provider_key: str | None = None,
                                   credential_slot: str = 'primary') -> bool:
    """Lift an active hold after a provider, endpoint, or credential change.

    A hold belongs to the account and endpoint that returned the 429, so it
    is meaningless once those change. provider_key=None (a settings change
    that could touch more than one account, or the caller cannot isolate
    which one) lifts only the legacy unscoped hold, matching pre-scoping
    behavior. A real provider_key with credential_slot='primary' also lifts
    the pre-slot per-provider marker and the fully-unscoped legacy one,
    since an unmigrated legacy hold may belong to it. credential_slot=
    'secondary' lifts only that slot's own marker: the legacy markers can
    only ever belong to primary (secondary did not exist before slot
    scoping), so clearing them here would risk lifting an unrelated,
    still-active primary hold. Returns whether any hold was lifted.
    """
    was_active, held_since = get_active_hold(db, provider_key, credential_slot)
    if not was_active:
        return False
    for suffix in _hold_chain(provider_key, credential_slot):
        clear_hold(db, suffix)
    logger.info(f"Rate-limit hold: queue pause lifted ({reason})")
    fire_queue_resumed_event(held_since=held_since)
    return True


def clear_hold_if_unchanged(db, hold_until: str,
                            provider_key: str | None = None) -> tuple[bool, str | None]:
    """Drop provider_key's pause only while its marker still reads `hold_until`.

    A 429 landing between a caller's read and this call owns a newer marker,
    and resuming on it would put the queue straight back into the limit.
    """
    since_key = _hold_since_key(provider_key)
    held_since = db.get_setting(since_key)
    if not db.clear_setting_if_equal(_hold_until_key(provider_key), hold_until):
        return False, None
    db.clear_setting(since_key)
    db.clear_setting(_hold_manual_key(provider_key))
    db.clear_setting(RATE_LIMIT_PROBE_AT_KEY)
    return True, held_since


def hold_is_active(hold_until: str | None) -> bool:
    """True when `hold_until` is a reset time still in the future."""
    reset_at = parse_iso_utc(hold_until) if hold_until else None
    return bool(reset_at and reset_at > utc_now())


def get_active_hold(db, provider_key: str | None = None,
                    credential_slot: str = 'primary') -> tuple[str | None, str | None]:
    """(hold_until, hold_since) while (provider_key, credential_slot)'s pause
    is active, else (None, None). provider_key=None checks only the legacy
    unscoped marker.

    A real provider_key with credential_slot='primary' also falls back, in
    order, to the pre-slot per-provider marker and the fully-unscoped
    legacy marker when its own marker is not active: a hold recorded before
    provider- or slot-scoping (or by a caller that could not resolve a
    provider) still blocks primary for one release. 'secondary' has no such
    fallback (see _hold_chain).

    A marker past its reset waits on the processor's next pass to be
    cleared; readers see no hold at all in that gap.
    """
    for suffix in _hold_chain(provider_key, credential_slot):
        hold_until = get_hold_until(db, suffix)
        if hold_is_active(hold_until):
            try:
                return hold_until, db.get_setting(_hold_since_key(suffix)) or None
            except Exception:
                return hold_until, None
    return None, None


def is_queue_paused(db, provider_key: str | None = None,
                    credential_slot: str = 'primary') -> bool:
    """True while (provider_key, credential_slot)'s recorded hold (or its
    legacy fallback markers, for credential_slot='primary') has a reset time
    still in the future. provider_key=None checks only the legacy unscoped
    marker."""
    return get_active_hold(db, provider_key, credential_slot)[0] is not None


def hold_message(hold_until: str | None, error) -> str:
    """error_message written on an episode a 429 sent back to the queue.

    hold_until is None when the hold feature is off: nothing was paused.
    """
    if not hold_until:
        return f"LLM rate limit: {error}"
    return f"Paused (LLM rate limit until {hold_until}): {error}"


def rate_limit_hold_tick(db) -> None:
    """Clear every pause marker (legacy and provider-scoped) whose reset
    time has passed, firing one resumed event per marker actually cleared.
    """
    _tick_one(db, None)
    for provider in _provider_hold_suffixes(db):
        _tick_one(db, provider)


def _tick_one(db, provider_key: str | None) -> None:
    hold_until = get_hold_until(db, provider_key)
    if not hold_until or hold_is_active(hold_until):
        return
    cleared, held_since = clear_hold_if_unchanged(db, hold_until, provider_key)
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
                                  podcast_name: str,
                                  provider_key: str | None = None,
                                  credential_slot: str = 'primary') -> str | None:
    """Pause (provider_key, credential_slot)'s queue for a 429 and alert once
    per pause; returns the effective hold_until, or None when the hold
    feature is off.

    provider_key should be the provider whose call actually 429'd (e.g.
    error.provider_key), and credential_slot which account on it (e.g.
    error.credential_slot); provider_key=None falls back to the legacy
    unscoped marker when the caller cannot resolve it, pausing every
    provider for one release.

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
    hold_until, started = record_hold_until(
        db, provider_key, hold_until_iso, credential_slot=credential_slot)
    logger.warning(f"[{slug}:{episode_id}] Rate-limit hold: paused until "
                   f"{hold_until} (provider reset)")
    # One alert per pause: a later 429 under it only moves the reset out.
    if started:
        fire_queue_held_event(hold_until=hold_until, error_message=error, slug=slug,
                              episode_id=episode_id, podcast_name=podcast_name)
    return hold_until


def _rate_limit_setting_keys(credential_slot: str) -> tuple[str, str, str]:
    """(rpm_key, rpd_key, tpm_key) for a slot: secondary reads secondary_*."""
    if credential_slot == 'secondary':
        return ('secondary_provider_requests_per_min',
                'secondary_provider_requests_per_day',
                'secondary_provider_tokens_per_min')
    return ('provider_requests_per_min', 'provider_requests_per_day',
            'provider_tokens_per_min')


def evaluate_provider_rate_limit(db, provider_key: str,
                                 credential_slot: str = 'primary') -> str | None:
    """Reset time when (provider_key, credential_slot) is at or over its
    manual RPM/RPD/TPM cap, else None (no side effects).

    RPM: 60s after the oldest attempt in the last 60s. TPM: 60s after the
    oldest token-contributing finalized row in the last 60s. RPD: next UTC
    midnight. Returns the latest reset of whichever caps trip. 0 for a limit
    disables it; all 0 short-circuits to None. Capped at MAX_HOLD.
    """
    rpm_key, rpd_key, tpm_key = _rate_limit_setting_keys(credential_slot)
    rpm = get_env_backed_int(rpm_key, floor=0)
    rpd = get_env_backed_int(rpd_key, floor=0)
    tpm = get_env_backed_int(tpm_key, floor=0)
    if rpm <= 0 and rpd <= 0 and tpm <= 0:
        return None
    now = utc_now()
    minute_since = (now - timedelta(seconds=60)).strftime(ISO_FORMAT)
    reset = None
    if rpm > 0:
        if db.count_recent_llm_attempts(provider_key, credential_slot, minute_since) >= rpm:
            oldest = db.oldest_recent_llm_attempt(provider_key, credential_slot, minute_since)
            oldest_dt = parse_iso_utc(oldest) if oldest else now
            reset = oldest_dt + timedelta(seconds=60)
    if tpm > 0:
        if db.sum_recent_llm_tokens(provider_key, credential_slot, minute_since) >= tpm:
            oldest = db.oldest_recent_llm_token_attempt(provider_key, credential_slot, minute_since)
            oldest_dt = parse_iso_utc(oldest) if oldest else now
            tpm_reset = oldest_dt + timedelta(seconds=60)
            reset = max(reset, tpm_reset) if reset is not None else tpm_reset
    if rpd > 0:
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        day_since = midnight.strftime(ISO_FORMAT)
        if db.count_recent_llm_attempts(provider_key, credential_slot, day_since) >= rpd:
            next_midnight = midnight + timedelta(days=1)
            reset = max(reset, next_midnight) if reset is not None else next_midnight
    if reset is None:
        return None
    return _capped_reset_iso(reset)


def enforce_provider_rate_limit(db, provider_key: str,
                                credential_slot: str = 'primary', *,
                                slug: str | None = None,
                                episode_id: str | None = None,
                                podcast_name: str | None = None) -> str | None:
    """Record a provider+slot hold when the manual RPM/RPD cap is reached;
    returns the effective hold_until, or None when under the cap.

    Uses the same record_hold_until marker a 429 hold uses, so is_queue_paused
    and the probe tick treat it uniformly. Independent of the 429-hold toggle:
    the RPM/RPD settings are their own switch.
    """
    reset_iso = evaluate_provider_rate_limit(db, provider_key, credential_slot)
    if reset_iso is None:
        return None
    hold_until, started = record_hold_until(
        db, provider_key, reset_iso, credential_slot=credential_slot, manual=True)
    if started:
        logger.warning(f"Manual rate limit: paused {provider_key}:{credential_slot} "
                       f"until {hold_until}")
        if slug and episode_id:
            fire_queue_held_event(hold_until=hold_until,
                                  error_message=f"manual rate limit ({provider_key})",
                                  slug=slug, episode_id=episode_id,
                                  podcast_name=podcast_name)
    return hold_until


def _warn_probe_failure(hold_until: str, message: str) -> None:
    """WARNING once per hold_until (repeat failures log at DEBUG)."""
    if hold_until in _warned_probe_holds:
        logger.debug(message)
        return
    _warned_probe_holds.add(hold_until)
    logger.warning(message)


def _clear_probed_hold(db, provider_key: str | None) -> str | None:
    """Clear whichever primary-slot marker (new-format, pre-slot legacy, or
    fully-unscoped legacy) the probe found active: probing always tests the
    primary slot's credentials, via get_llm_client()."""
    held_since = None
    for suffix in _hold_chain(provider_key, 'primary'):
        since = clear_hold(db, suffix)
        held_since = held_since or since
    return held_since


def _probe_usage_url(db, usage_url: str, hold_until: str,
                     provider_key: str | None) -> bool | None:
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
        held_since = _clear_probed_hold(db, provider_key)
        fire_queue_resumed_event(held_since=held_since)
        logger.info("Rate-limit probe: usage endpoint reports clear; resuming queue")
        return True
    if blocked is True:
        reset_iso = usage_reset_iso(payload)
        if reset_iso is not None:
            record_hold_until(db, provider_key, reset_iso, force=True)
            logger.info(f"Rate-limit probe: usage endpoint re-stamped hold to {reset_iso}")
            return True
        _warn_probe_failure(
            hold_until, f"Rate-limit probe: usage URL blocked with no usable reset ({usage_url})")
        return None
    _warn_probe_failure(
        hold_until, f"Rate-limit probe: usage URL payload missing boolean 'blocked' ({usage_url})")
    return None


def _probe_via_completion(db, provider_key: str | None) -> bool:
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
                record_hold_until(db, provider_key, hold_until_iso, force=True)
                logger.info(f"Rate-limit probe: completion probe re-stamped hold to {hold_until_iso}")
            return False
        logger.debug(f"Rate-limit probe: completion probe failed, leaving hold: {e}")
        return False
    held_since = _clear_probed_hold(db, provider_key)
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


def _active_hold_source(db) -> tuple[str | None, str | None, str | None]:
    """(hold_until, provider_key, suffix) for the marker the probe should act
    on; suffix is the literal stored key suffix (for the manual-flag lookup).

    The probe only ever tests the primary slot (get_llm_client() resolves
    the primary credentials), so this walks the primary-slot fallback
    chain: the effective (currently in-use) provider's own slot-scoped
    marker wins when active, else the pre-slot per-provider marker, else
    the fully-unscoped legacy marker. A single-provider install (whose real
    holds land on the slot-scoped key today) and a not-yet-migrated hold
    both keep working exactly as before.
    """
    provider = get_effective_provider()
    for suffix in _hold_chain(provider, 'primary'):
        hold_until = get_hold_until(db, suffix)
        if hold_is_active(hold_until):
            return hold_until, (provider if suffix is not None else None), suffix
    return None, None, None


def _hold_is_manual(db, suffix: str | None) -> bool:
    """True when suffix's hold is a manual MinusPod cap (#747)."""
    try:
        return bool(db.get_setting(_hold_manual_key(suffix)))
    except Exception:
        return False


def _probe_rate_limit(db) -> bool:
    hold_until, provider_key, suffix = _active_hold_source(db)
    if not hold_until:
        return False
    # A manual cap is our own accounting, not real provider throttling. A
    # completion probe would send a real uncounted request that clears the
    # hold early and burns the quota the cap protects, so never probe it: it
    # clears only by time when the tick sees its reset pass.
    if _hold_is_manual(db, suffix):
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
        result = _probe_usage_url(db, usage_url, hold_until, provider_key)
        if result is not None:
            return result

    return _probe_via_completion(db, provider_key)
