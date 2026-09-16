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
one slot must not pause the other same-type account. Both slots fall back
to the fully-unscoped marker, which is a blanket pause; only the pre-slot
per-provider marker is primary-only, since secondary did not exist while
that key was being written.

While a hold is active, probe_rate_limit() periodically re-checks it: an
operator-configured usage URL (tier 1) or a minimal LLM completion (tier 2)
can clear the hold early or re-stamp it with a fresher reset, since the
429's own stated reset can be wrong in either direction. The probe acts on
whichever marker is the active source, on any (provider, slot), and sends
its completion through a client and model routed to that pair. The
dispatcher's blanket pause and the tick's cleanup react to any active
hold, legacy or provider-scoped, as a unit.
"""
import logging
from datetime import timedelta

from config import coerce_bool_setting, get_env_backed_int
from database.settings import registry_current_value, registry_default
from llm_client import (
    extract_retry_after, get_client_for_provider, get_effective_provider,
    is_rate_limit_error,
)
from llm_route import resolve_route
import run_context
from utils.safe_http import safe_get, URLTrust
from utils.time import ISO_FORMAT, epoch_to_iso, parse_iso_utc, utc_now, utc_now_iso
from webhook_service import fire_queue_held_event, fire_queue_resumed_event

logger = logging.getLogger('podcast.refresh')

HOLD_UNTIL_KEY = 'rate_limit_hold_until'
HOLD_SINCE_KEY = 'rate_limit_hold_since'
# Marks a hold as a manual MinusPod cap (#747), not a real provider 429.
# Manual caps clear only by time; completion-probing them would burn the
# very quota they protect (see _probe_rate_limit).
HOLD_MANUAL_KEY = 'rate_limit_hold_manual'
RATE_LIMIT_PROBE_AT_KEY = 'rate_limit_probe_at'

# Why a job is not admitted, as reported by the queue admission explanation.
HOLD_REASON_MANUAL = 'manual_rate_limit'
HOLD_REASON_PROVIDER = 'provider_rate_limit'
HOLD_REASON_ACCOUNT_CHANGED = 'provider_account_changed'
HOLD_REASON_PAUSED = 'processing_paused'

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


def _hold_since(db, provider_key: str | None) -> str | None:
    """When this marker's pause began; None when the settings read fails."""
    try:
        return db.get_setting(_hold_since_key(provider_key)) or None
    except Exception:
        return None


def _hold_manual_key(provider_key: str | None) -> str:
    return HOLD_MANUAL_KEY if provider_key is None else f'{HOLD_MANUAL_KEY}:{provider_key}'


def _probe_at_key(provider_key: str | None) -> str:
    """Probe cadence stamp for one marker: one provider's probe must not
    silence another's."""
    return (RATE_LIMIT_PROBE_AT_KEY if provider_key is None
            else f'{RATE_LIMIT_PROBE_AT_KEY}:{provider_key}')


def _slot_key(provider_key: str, credential_slot: str) -> str:
    """Marker suffix for one (provider, credential_slot) pair."""
    return f'{provider_key}:{credential_slot}'


def _hold_chain(provider_key: str | None, credential_slot: str) -> list[str | None]:
    """Marker suffixes to check for (provider_key, credential_slot), most
    specific first.

    provider_key=None means only the fully-unscoped legacy marker. Both
    slots fall back to that marker: it is a blanket pause recorded by a
    caller that could not resolve a provider, so it applies to every
    account. The pre-slot per-provider marker is primary-only; secondary
    did not exist while that key was being written.
    """
    if provider_key is None:
        return [None]
    chain: list[str | None] = [_slot_key(provider_key, credential_slot)]
    if credential_slot == 'primary':
        chain.append(provider_key)
    chain.append(None)
    return chain


def _clear_chain(provider_key: str | None, credential_slot: str) -> list[str | None]:
    """Marker suffixes a change to (provider_key, credential_slot) may lift.

    Never the blanket unscoped marker for 'secondary': that hold pauses
    secondary but can belong to primary, so lifting it here would resume
    the queue straight back into a live limit. Primary does lift it: in-run
    429s now carry their (provider, slot), so a blanket marker is a legacy
    primary artifact.
    """
    chain = _hold_chain(provider_key, credential_slot)
    if credential_slot == 'secondary':
        return [suffix for suffix in chain if suffix is not None]
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
    """Drop provider_key's pause marker, start stamp, and probe cadence
    stamp; returns when that hold began."""
    since_key = _hold_since_key(provider_key)
    held_since = db.get_setting(since_key)
    db.clear_setting(_hold_until_key(provider_key))
    db.clear_setting(since_key)
    db.clear_setting(_hold_manual_key(provider_key))
    db.clear_setting(_probe_at_key(provider_key))
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


def active_held_pairs(db) -> set[tuple[str, str]]:
    """Active provider-scoped holds as (provider_key, credential_slot) pairs.

    Excludes the legacy unscoped marker (which pauses everything and is
    handled separately). Lets the dispatcher skip only the queue entries
    whose required accounts are held, instead of pausing all work.
    """
    pairs: set[tuple[str, str]] = set()
    for suffix in _provider_hold_suffixes(db):
        if not hold_is_active(get_hold_until(db, suffix)):
            continue
        provider, _, slot = suffix.rpartition(':')
        if slot not in ('primary', 'secondary'):
            provider, slot = suffix, 'primary'
        pairs.add((provider, slot))
    return pairs


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
    'secondary' lifts only that slot's own marker (see _clear_chain).
    Returns whether any hold was lifted.
    """
    return clear_holds_for_provider_change(db, reason, [provider_key],
                                           credential_slot=credential_slot)


def _lift_hold(db, provider_key: str | None, credential_slot: str) -> tuple[bool, str | None]:
    """Clear the markers a change to (provider_key, credential_slot) may
    lift; returns whether one was active and when that pause began."""
    suffixes = _clear_chain(provider_key, credential_slot)
    active = [s for s in suffixes if hold_is_active(get_hold_until(db, s))]
    if not active:
        return False, None
    held_since = _hold_since(db, active[0])
    for suffix in suffixes:
        clear_hold(db, suffix)
    return True, held_since


def clear_holds_for_provider_change(db, reason: str, provider_keys, *,
                                    credential_slot: str = 'primary') -> bool:
    """clear_hold_for_provider_change across the accounts one save changed,
    firing a single queue-resumed event for the save rather than one per
    account. Returns whether any hold was lifted."""
    lifted = False
    held_since = None
    for provider_key in provider_keys:
        was_active, since = _lift_hold(db, provider_key, credential_slot)
        lifted = lifted or was_active
        held_since = held_since or since
    if not lifted:
        return False
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
    db.clear_setting(_probe_at_key(provider_key))
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
    provider) still blocks primary for one release. 'secondary' falls back
    only to the fully-unscoped marker (see _hold_chain).

    A marker past its reset waits on the processor's next pass to be
    cleared; readers see no hold at all in that gap.
    """
    for suffix in _hold_chain(provider_key, credential_slot):
        hold_until = get_hold_until(db, suffix)
        if hold_is_active(hold_until):
            return hold_until, _hold_since(db, suffix)
    return None, None


def is_queue_paused(db, provider_key: str | None = None,
                    credential_slot: str = 'primary') -> bool:
    """True while (provider_key, credential_slot)'s recorded hold, or one of
    its fallback markers (see _hold_chain), has a reset time still in the
    future. provider_key=None checks only the legacy unscoped marker."""
    return get_active_hold(db, provider_key, credential_slot)[0] is not None


def active_hold_reason(db, provider_key: str | None = None,
                       credential_slot: str = 'primary'
                       ) -> tuple[str | None, str | None]:
    """(reason, resumes_at) for (provider, slot)'s active hold, else (None, None).

    Reports which marker in the fallback chain is actually holding the pair,
    so a manual cap and a provider 429 are distinguishable in the UI.
    """
    for suffix in _hold_chain(provider_key, credential_slot):
        hold_until = get_hold_until(db, suffix)
        if not hold_is_active(hold_until):
            continue
        manual = coerce_bool_setting(db.get_setting(_hold_manual_key(suffix)))
        return (HOLD_REASON_MANUAL if manual else HOLD_REASON_PROVIDER), hold_until
    return None, None


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


def resolve_hold_scope(error, phase: str | None = None) -> tuple[str | None, str]:
    """Provider and slot a 429's hold is scoped to.

    The error's own provider wins, then the run's route for its phase, then a
    run whose phases all share one account, then the legacy unscoped marker.
    """
    provider_key = getattr(error, 'provider_key', None)
    slot = getattr(error, 'credential_slot', None) or 'primary'
    if provider_key:
        return provider_key, slot
    phase = phase or getattr(error, 'phase', None)
    if phase:
        route = run_context.route_for_phase(phase) or {}
        return route.get('provider_key'), route.get('credential_slot', 'primary')
    ctx = run_context.current()
    routes = (ctx.route_snapshot if ctx else None) or {}
    pairs = {(route['provider_key'], route.get('credential_slot', 'primary'))
             for route in routes.values()
             if isinstance(route, dict) and route.get('provider_key')}
    return pairs.pop() if len(pairs) == 1 else (None, slot)


def hold_queue_for_provider_limit(db, error, *, slug: str, episode_id: str,
                                  podcast_name: str,
                                  provider_key: str | None = None,
                                  credential_slot: str | None = None,
                                  phase: str | None = None) -> str | None:
    """Pause the 429'd account's queue and alert once per pause; returns the
    effective hold_until, or None when the hold feature is off.

    The scope comes from resolve_hold_scope unless the caller passes an
    explicit provider_key; `phase` names the pipeline phase to fall back to
    when the error carries none.

    A configured usage endpoint is preferred over the 429's own stated reset,
    which can be wrong in either direction.
    """
    if not is_rate_limit_hold_enabled(db):
        return None
    if provider_key is None:
        provider_key, credential_slot = resolve_hold_scope(error, phase)
    credential_slot = credential_slot or 'primary'
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


def manual_rate_limit_caps(credential_slot: str = 'primary') -> dict:
    """{rpm, rpd, tpm, minute_since, day_since} for one account slot.

    The single definition of the manual caps and their windows, shared by the
    pre-start evaluation and the atomic per-request reservation, so the two
    can never disagree on what counts as "inside the window". All-zero caps
    mean no manual limit is configured.
    """
    rpm_key, rpd_key, tpm_key = _rate_limit_setting_keys(credential_slot)
    now = utc_now()
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return {
        'rpm': get_env_backed_int(rpm_key, floor=0),
        'rpd': get_env_backed_int(rpd_key, floor=0),
        'tpm': get_env_backed_int(tpm_key, floor=0),
        'minute_since': (now - timedelta(seconds=60)).strftime(ISO_FORMAT),
        'day_since': midnight.strftime(ISO_FORMAT),
    }


def _cap_reset_iso(blocked: str, oldest: str | None) -> str:
    """Reset time for the cap that refused a request reservation."""
    now = utc_now()
    if blocked == 'rpd':
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return _capped_reset_iso(midnight + timedelta(days=1))
    oldest_dt = (parse_iso_utc(oldest) if oldest else None) or now
    return _capped_reset_iso(oldest_dt + timedelta(seconds=60))


def reserve_provider_request(db, provider_key: str,
                             credential_slot: str = 'primary', *,
                             attempt: dict) -> tuple[str | None, str | None]:
    """Take one request slot for (provider, slot) by creating its ledger
    attempt inside the cap check; returns (attempt_id, hold_until).

    attempt_id is None when a manual cap refused the request, and hold_until
    is then the pause this recorded. Unlike evaluate_provider_rate_limit,
    counting and reserving are one transaction, so concurrent workers cannot
    all pass the same check.
    """
    caps = manual_rate_limit_caps(credential_slot)
    result = db.reserve_llm_attempt(provider_key=provider_key,
                                    credential_slot=credential_slot,
                                    caps=caps, **attempt)
    if result['attempt_id'] is not None:
        return result['attempt_id'], None
    reset_iso = _cap_reset_iso(result['blocked'], result['oldest'])
    hold_until, started = record_hold_until(
        db, provider_key, reset_iso, credential_slot=credential_slot, manual=True)
    if started:
        logger.warning(f"Manual rate limit ({result['blocked']}): paused "
                       f"{provider_key}:{credential_slot} until {hold_until}")
    return None, hold_until


def evaluate_provider_rate_limit(db, provider_key: str,
                                 credential_slot: str = 'primary') -> str | None:
    """Reset time when (provider_key, credential_slot) is at or over its
    manual RPM/RPD/TPM cap, else None (no side effects).

    RPM counts SDK dispatches in the last 60s and resets 60s after the oldest
    of them; TPM counts reserved-or-actual tokens over the same window; RPD
    resets at the next UTC midnight. Returns the latest reset of whichever
    caps trip. 0 for a limit disables it; all 0 short-circuits to None.
    Capped at MAX_HOLD. This is the pre-start check: the per-request
    reservation in reserve_provider_request is what actually enforces a cap
    against concurrent workers.
    """
    caps = manual_rate_limit_caps(credential_slot)
    rpm, rpd, tpm = caps['rpm'], caps['rpd'], caps['tpm']
    if rpm <= 0 and rpd <= 0 and tpm <= 0:
        return None
    now = utc_now()
    minute_since = caps['minute_since']
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
        if db.count_recent_llm_attempts(provider_key, credential_slot, caps['day_since']) >= rpd:
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


def _probe_usage_url(db, usage_url: str, hold_until: str,
                     provider_key: str | None, credential_slot: str) -> bool | None:
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
        _, held_since = _lift_hold(db, provider_key, credential_slot)
        fire_queue_resumed_event(held_since=held_since)
        logger.info("Rate-limit probe: usage endpoint reports clear; resuming queue")
        return True
    if blocked is True:
        reset_iso = usage_reset_iso(payload)
        if reset_iso is not None:
            record_hold_until(db, provider_key, reset_iso,
                              credential_slot=credential_slot, force=True)
            logger.info(f"Rate-limit probe: usage endpoint re-stamped hold to {reset_iso}")
            return True
        _warn_probe_failure(
            hold_until, f"Rate-limit probe: usage URL blocked with no usable reset ({usage_url})")
        return None
    _warn_probe_failure(
        hold_until, f"Rate-limit probe: usage URL payload missing boolean 'blocked' ({usage_url})")
    return None


def _probe_candidate_routes() -> list:
    """Every route the pipeline would use right now, review included.

    Review inherits the invoking pass when review_provider is same_as_pass,
    so it is resolved once per pass rather than once overall.
    """
    routes = []
    for phase in ('detection', 'verification', 'chapters'):
        try:
            routes.append(resolve_route(phase))
        except Exception as e:
            logger.debug(f"Rate-limit probe: {phase} route unresolved: {e}")
    for pass_route in [r for r in routes if r.phase in ('detection', 'verification')]:
        try:
            routes.append(resolve_route(
                'review', pass_model=pass_route.model_id,
                pass_provider=pass_route.provider_key,
                pass_base_url=pass_route.base_url,
                pass_credential_slot=pass_route.credential_slot))
        except Exception as e:
            logger.debug(f"Rate-limit probe: review route unresolved: {e}")
    return routes


def _probe_route_target(provider_key: str, credential_slot: str) -> tuple[str | None, str | None]:
    """(model_id, base_url) of a stage routed to (provider_key,
    credential_slot), or (None, None) when no stage targets that pair."""
    for route in _probe_candidate_routes():
        if route.provider_key == provider_key and route.credential_slot == credential_slot:
            return route.model_id, route.base_url
    return None, None


def _probe_via_completion(db, provider_key: str | None, credential_slot: str) -> bool:
    """Tier 2: one minimal completion against the held (provider, slot).

    The probe is a real, billable request, so it is recorded in the ledger
    under phase_key 'probe' with no episode/run, the same as any other
    dispatch. A pair no stage routes to is left to expire by wall clock
    rather than probed with another account's client and model. Manual caps
    are never completion-probed (see _probe_rate_limit).
    """
    target_provider = provider_key or get_effective_provider()
    model, base_url = _probe_route_target(target_provider, credential_slot)
    if not model:
        logger.debug(f"Rate-limit probe: no stage routes to "
                     f"{target_provider}:{credential_slot}; leaving hold")
        return False
    attempt_id = db.begin_llm_attempt(
        run_id=None, podcast_id=None, episode_id=None, phase_key='probe',
        invoking_pass=None, provider_key=target_provider,
        credential_slot=credential_slot, configured_model=model,
        window_label='rate_limit_probe')
    try:
        client = get_client_for_provider(target_provider, base_url=base_url,
                                         credential_slot=credential_slot)
        response = client.messages_create(
            model=model, max_tokens=1, system='',
            messages=[{"role": "user", "content": "hi"}],
            timeout=PROBE_TIMEOUT_SECONDS,
        )
    except Exception as e:
        db.finalize_llm_attempt(attempt_id, state='failure')
        if is_rate_limit_error(e):
            hold_after = extract_retry_after(e, max_seconds=MAX_RESET_SECONDS)
            if hold_after is not None:
                hold_until_iso = (utc_now() + timedelta(seconds=max(0.0, hold_after))).strftime(ISO_FORMAT)
                record_hold_until(db, provider_key, hold_until_iso,
                                  credential_slot=credential_slot, force=True)
                logger.info(f"Rate-limit probe: completion probe re-stamped hold to {hold_until_iso}")
            return False
        logger.debug(f"Rate-limit probe: completion probe failed, leaving hold: {e}")
        return False
    db.finalize_llm_attempt_from_response(attempt_id, 'success', response)
    _, held_since = _lift_hold(db, provider_key, credential_slot)
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


def _active_hold_sources(db) -> list[tuple[str, str | None, str, str | None]]:
    """Every active hold the probe could act on, as (hold_until,
    provider_key, credential_slot, suffix); suffix is the literal stored key
    suffix (for the manual-flag and cadence lookups).

    The effective provider's primary chain comes first, so a
    single-provider install and a not-yet-migrated hold behave as before.
    Each held pair is its own entry with its own cadence stamp, so a manual
    cap or a hold inside its window cannot starve the rest.
    """
    provider = get_effective_provider()
    primary_chain = _hold_chain(provider, 'primary')
    sources: list[tuple[str, str | None, str, str | None]] = []
    for suffix in primary_chain:
        hold_until = get_hold_until(db, suffix)
        if hold_is_active(hold_until):
            sources.append((hold_until, (provider if suffix is not None else None),
                            'primary', suffix))
            break
    for suffix in _provider_hold_suffixes(db):
        if suffix in primary_chain:
            continue
        hold_until = get_hold_until(db, suffix)
        if not hold_is_active(hold_until):
            continue
        held_provider, _, slot = suffix.rpartition(':')
        if slot not in ('primary', 'secondary'):
            held_provider, slot = suffix, 'primary'
        sources.append((hold_until, held_provider, slot, suffix))
    return sources


def _is_effective_primary(provider_key: str | None, credential_slot: str) -> bool:
    """True for the global provider's primary account, the only one the
    single llm_usage_url setting can describe."""
    return credential_slot == 'primary' and (
        provider_key is None or provider_key == get_effective_provider())


def _probe_due(db, suffix: str | None, minutes: int) -> bool:
    """True when this marker's own cadence window has elapsed."""
    last_probe_at = parse_iso_utc(db.get_setting(_probe_at_key(suffix)))
    return not (last_probe_at
                and (utc_now() - last_probe_at).total_seconds() < minutes * 60)


def _hold_is_manual(db, suffix: str | None) -> bool:
    """True when suffix's hold is a manual MinusPod cap (#747)."""
    try:
        return bool(db.get_setting(_hold_manual_key(suffix)))
    except Exception:
        return False


def _probe_rate_limit(db) -> bool:
    minutes = get_rate_limit_probe_minutes(db)
    if minutes <= 0:
        return False
    for hold_until, provider_key, credential_slot, suffix in _active_hold_sources(db):
        # Never completion-probe a manual cap; it clears by time (see
        # HOLD_MANUAL_KEY). Skip to the next pair rather than ending the pass.
        if _hold_is_manual(db, suffix) or not _probe_due(db, suffix, minutes):
            continue
        db.set_setting(_probe_at_key(suffix), utc_now_iso())

        usage_url = get_llm_usage_url(db)
        if usage_url and _is_effective_primary(provider_key, credential_slot):
            result = _probe_usage_url(db, usage_url, hold_until, provider_key,
                                      credential_slot)
            if result is not None:
                return result

        return _probe_via_completion(db, provider_key, credential_slot)
    return False
