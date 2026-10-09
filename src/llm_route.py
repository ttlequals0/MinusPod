"""Per-phase LLM route resolution: (provider, model, base_url) per pipeline
phase, so detection/verification/review/chapters can each target a
different provider. Credentials are resolved separately at call time by
provider_key/credential_slot; Route never carries a secret.

Each stage setting (detection_provider, verification_provider,
chapters_provider, review_provider) stores a SLOT (primary/secondary), not a
provider type: 'primary' resolves through the global llm_provider config,
'secondary' through the optional secondary_provider_* settings. A stage
referencing 'secondary' while secondary_provider_enabled is false falls back
to primary (fail-safe) and logs once per stage.
"""
from __future__ import annotations
import hashlib
import logging
from collections.abc import Callable
from dataclasses import dataclass

import failover
from config import (
    ModelNotConfiguredError, PROVIDER_ANTHROPIC, PROVIDER_OPENROUTER,
    PROVIDERS_NON_ANTHROPIC, OPENROUTER_BASE_URL, DEFAULT_OPENAI_BASE_URL,
    coerce_bool_setting,
)
import database
import llm_client
import run_context

logger = logging.getLogger(__name__)

PHASES = ('detection', 'review', 'verification', 'chapters', 'pattern_cleanup')

SAME_AS_PASS = 'same_as_pass'
SAME_AS_DETECTION = 'same_as_detection'
SLOT_PRIMARY = 'primary'
SLOT_SECONDARY = 'secondary'
SLOT_FAILOVER = 'failover'
VALID_SLOTS = (SLOT_PRIMARY, SLOT_SECONDARY)
ALL_CREDENTIAL_SLOTS = (SLOT_PRIMARY, SLOT_SECONDARY, SLOT_FAILOVER)

# Stages already warned about a secondary-disabled/misconfigured fallback,
# so a busy queue logs once instead of once per resolve_route call.
_warned_secondary_fallback: set[str] = set()


@dataclass(frozen=True)
class Route:
    phase: str
    provider_key: str
    model_id: str
    base_url: str | None  # non-secret; never includes embedded credentials
    slot: str  # resolved primary/secondary, after inheritance and fallback
    credential_slot: str  # which secret to read: primary=type secret, secondary=secondary_provider_api_key
    # Non-secret identity of the account this route was resolved against
    # (provider type + endpoint). None only for routes built before this
    # field existed; see account_identity_for_slot.
    account_id: str | None = None


class _CachedProviderSettings:
    """Settings reader backed by llm_client's provider TTL cache.

    Account identity must be read from the same source the credential is,
    or a save seen by one and not the other reopens the mismatch this
    identity exists to catch.
    """

    @staticmethod
    def get_setting(key: str) -> str | None:
        return llm_client._get_cached_setting(key)


_CACHED_SETTINGS = _CachedProviderSettings()


def account_identity(provider_key: str | None, base_url: str | None) -> str | None:
    """Non-secret identity of a provider account: provider type + endpoint.

    A key rotation within the same account leaves this unchanged, which is
    what lets rotation keep working while a provider or endpoint switch
    invalidates every route frozen against the old account.
    """
    if not provider_key:
        return None
    raw = f"{provider_key}|{base_url or ''}"
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]


def account_identity_for_primary_provider(provider_key: str | None) -> str | None:
    """Identity of the primary slot's account for one provider TYPE.

    The primary slot keys its credential by provider type (anthropic_api_key,
    openai_api_key, and so on), so a primary route's key follows the route's
    own provider, not whichever type is globally selected. Only the
    configurable endpoint can move that account.
    """
    if not provider_key:
        return None
    return account_identity(provider_key, _base_url_for_primary(provider_key))


def account_identity_for_slot(credential_slot: str, db=None) -> str | None:
    """The identity `credential_slot` points at right now, or None when the
    slot has no provider type configured.

    Read through the credential cache by default so it cannot disagree with
    the key request-time code resolves. Deliberately skips
    _resolve_slot_config's secondary-to-primary fallback: a secondary slot
    that lost its provider type is a changed account, not primary's account
    under another name.
    """
    db = db or _CACHED_SETTINGS
    if credential_slot == SLOT_SECONDARY:
        provider = db.get_setting('secondary_provider')
        if not provider:
            return None
        return account_identity(provider, _base_url_for_secondary(db, provider))
    if credential_slot == SLOT_FAILOVER:
        cfg = failover.failover_llm_config()
        if not cfg['provider']:
            return None
        return account_identity(cfg['provider'], _failover_base_url(cfg['provider'], cfg['base_url']))
    return account_identity_for_primary_provider(llm_client.get_effective_provider())


def current_account_identity(provider_key: str | None,
                             credential_slot: str) -> str | None:
    """Current account identity: primary follows the provider type, other slots their configuration."""
    if credential_slot == SLOT_SECONDARY:
        return account_identity_for_slot(SLOT_SECONDARY)
    if credential_slot == SLOT_FAILOVER:
        return account_identity_for_slot(SLOT_FAILOVER)
    return account_identity_for_primary_provider(provider_key)


def route_account_mismatch(route: 'Route | dict') -> tuple[str, str | None] | None:
    """(frozen_account_id, current_account_id) when `route` was resolved
    against an account its credential no longer belongs to, else None.

    A route with no account_id (resolved before this field existed) is not
    checked: there is nothing to compare it against.
    """
    if isinstance(route, Route):
        frozen = route.account_id
        provider_key = route.provider_key
        credential_slot = route.credential_slot
    else:
        frozen = route.get('account_id')
        provider_key = route.get('provider_key')
        credential_slot = route.get('credential_slot', SLOT_PRIMARY)
    if not frozen:
        return None
    current = current_account_identity(provider_key, credential_slot)
    return None if current == frozen else (frozen, current)


def client_for_route(route: str | Route | dict | None, *,
                      override: llm_client.LLMClient | None = None,
                      fallback: Callable[[], llm_client.LLMClient | None] | None = None
                      ) -> llm_client.LLMClient | None:
    """Override wins, then the route's provider client, then fallback().
    `route` is a phase name, snapshot dict, Route, or None; fallback stays lazy.

    Raises ProviderAccountChangedError when the route's frozen account no
    longer matches its slot: building here would pair the slot's current key
    with the route's frozen endpoint.
    """
    if override is not None:
        return override
    if isinstance(route, str):
        route = run_context.route_for_phase(route)
    if not route:
        return fallback() if fallback is not None else None
    if isinstance(route, Route):
        phase = route.phase
        provider_key = route.provider_key
        base_url = route.base_url
        credential_slot = route.credential_slot
    else:
        phase = route.get('phase')
        provider_key = route['provider_key']
        base_url = route.get('base_url')
        credential_slot = route.get('credential_slot', SLOT_PRIMARY)
    mismatch = route_account_mismatch(route)
    if mismatch is not None:
        frozen, current = mismatch
        raise llm_client.ProviderAccountChangedError(
            f"{credential_slot} provider account changed since this run "
            f"resolved its routes; not sending the current key to the "
            f"route's frozen endpoint",
            credential_slot=credential_slot, phase=phase,
            expected_account_id=frozen, current_account_id=current)
    return llm_client.get_client_for_provider(provider_key, base_url=base_url,
                                   credential_slot=credential_slot)


def _warn_secondary_fallback_once(stage: str) -> None:
    if stage in _warned_secondary_fallback:
        return
    _warned_secondary_fallback.add(stage)
    logger.warning(
        "%s references the secondary provider slot but it is disabled or "
        "unconfigured; falling back to primary.", stage)


def _secondary_enabled(db) -> bool:
    return coerce_bool_setting(db.get_setting('secondary_provider_enabled'))


def _base_url_for_primary(provider: str) -> str | None:
    """Non-secret endpoint for the primary slot, mirroring llm_client._build_client."""
    if provider == PROVIDER_ANTHROPIC:
        return None
    if provider == PROVIDER_OPENROUTER:
        return OPENROUTER_BASE_URL
    if provider in PROVIDERS_NON_ANTHROPIC:
        return llm_client._normalize_base_url_for_provider(provider, llm_client.get_effective_base_url())
    return None


def _base_url_for_secondary(db, provider: str) -> str | None:
    """Non-secret endpoint for the secondary slot."""
    if provider == PROVIDER_ANTHROPIC:
        return None
    if provider == PROVIDER_OPENROUTER:
        return OPENROUTER_BASE_URL
    if provider in PROVIDERS_NON_ANTHROPIC:
        raw = db.get_setting('secondary_provider_base_url') or DEFAULT_OPENAI_BASE_URL
        return llm_client._normalize_base_url_for_provider(provider, raw)
    return None


def _failover_base_url(provider: str, raw: str | None) -> str | None:
    """Non-secret endpoint for the failover slot."""
    if provider == PROVIDER_ANTHROPIC:
        return None
    if provider == PROVIDER_OPENROUTER:
        return OPENROUTER_BASE_URL
    return llm_client._normalize_base_url_for_provider(provider, raw or DEFAULT_OPENAI_BASE_URL)


def _failover_route(phase: str | None, credential_slot: str) -> Route | None:
    """The failover Route for a phase on `credential_slot` while that slot is failed over."""
    target = failover.llm_target_for_slot(credential_slot)
    if target is None or not failover.is_active(target):
        return None
    cfg = failover.failover_llm_config()
    if not failover.is_configured(target, cfg):
        return None
    model = cfg['models'].get(phase) or cfg['models']['detection']
    base_url = _failover_base_url(cfg['provider'], cfg['base_url'])
    account_id = account_identity(cfg['provider'], base_url)
    ctx = run_context.current()
    if ctx is not None:
        # A standby account replaced mid-run then fails the account check and requeues.
        account_id = ctx.pin_failover_account(account_id)
    return Route(phase=phase, provider_key=cfg['provider'], model_id=model,
                 base_url=base_url, slot=SLOT_FAILOVER, credential_slot=SLOT_FAILOVER,
                 account_id=account_id)


def apply_failover(route: Route) -> Route:
    """The failover Route for `route` while its slot is failed over, else `route`."""
    return _failover_route(route.phase, route.credential_slot) or route


def apply_failover_dict(route: dict) -> dict:
    """Snapshot-dict form of apply_failover; marks the replaced slot in failover_from."""
    slot = route.get('credential_slot', SLOT_PRIMARY)
    fo = _failover_route(route.get('phase'), slot)
    if fo is None:
        return route
    return {**route, 'provider_key': fo.provider_key, 'configured_model': fo.model_id,
            'base_url': fo.base_url, 'credential_slot': SLOT_FAILOVER,
            'account_id': fo.account_id, 'failover_from': slot}


@dataclass(frozen=True)
class LiveRoute:
    route: Route | dict | None
    provider: str | None
    credential_slot: str
    model: str | None
    timeout: float
    max_retries: int


def live_route_from(route: Route | dict | None, model=None, llm_timeout=None,
                    max_retries=None) -> LiveRoute:
    """Request fields derived from an already-live route; the arguments are the no-route fallback."""
    if not route:
        return LiveRoute(None, None, SLOT_PRIMARY, model,
                         llm_client.get_llm_timeout() if llm_timeout is None else llm_timeout,
                         llm_client.get_llm_max_retries() if max_retries is None else max_retries)
    if isinstance(route, Route):
        provider, slot, route_model = route.provider_key, route.credential_slot, route.model_id
    else:
        provider = route['provider_key']
        slot = route.get('credential_slot', SLOT_PRIMARY)
        route_model = route.get('configured_model')
    return LiveRoute(route, provider, slot, route_model or model,
                     llm_client.get_llm_timeout(provider, slot), llm_client.get_llm_max_retries(provider, slot))


def live_route_params(phase: str, model=None, llm_timeout=None, max_retries=None) -> LiveRoute:
    """Read the live route and request settings together; arguments supply the outside-run fallback."""
    return live_route_from(run_context.route_for_phase(phase), model, llm_timeout, max_retries)


def _resolve_slot_config(db, slot: str) -> tuple[str, str | None, str]:
    """(provider_key, base_url, credential_slot) for an already-resolved
    slot. Falls back to primary, with a one-time warning, when 'secondary'
    has no provider type configured (an inconsistent state the settings API
    should normally prevent)."""
    if slot == SLOT_SECONDARY:
        provider = db.get_setting('secondary_provider')
        if provider:
            return provider, _base_url_for_secondary(db, provider), SLOT_SECONDARY
        _warn_secondary_fallback_once('secondary_provider')
    provider = llm_client.get_effective_provider()
    return provider, _base_url_for_primary(provider), SLOT_PRIMARY


def _detection_slot(db) -> str:
    """Detection stage's resolved slot: primary (default) or secondary."""
    value = db.get_setting('detection_provider') or SLOT_PRIMARY
    if value not in VALID_SLOTS:
        value = SLOT_PRIMARY
    if value == SLOT_SECONDARY and not _secondary_enabled(db):
        _warn_secondary_fallback_once('detection_provider')
        return SLOT_PRIMARY
    return value


def _inherited_slot(db, stage_key: str, inherit_slot: str) -> str:
    """verification/chapters slot: explicit primary/secondary,
    same_as_detection, or unset (both inherit detection's resolved slot)."""
    value = db.get_setting(stage_key)
    if not value or value == SAME_AS_DETECTION or value not in VALID_SLOTS:
        return inherit_slot
    if value == SLOT_SECONDARY and not _secondary_enabled(db):
        _warn_secondary_fallback_once(stage_key)
        return SLOT_PRIMARY
    return value


def resolved_stage_slot(db, stage: str) -> str:
    """The final primary/secondary slot `stage` resolves to right now,
    applying inheritance and the secondary-disabled fallback. Used by the
    settings API to decide whether a stage still tracks the global
    (primary) provider config, independent of resolve_route's per-call
    Database() instance.

    A same_as_pass review reports detection's slot, the pass it runs on
    first.
    """
    if stage == 'detection':
        slot = _detection_slot(db)
    elif stage in ('verification', 'chapters', 'pattern_cleanup'):
        slot = _inherited_slot(db, f'{stage}_provider', _detection_slot(db))
    elif stage == 'review':
        configured = db.get_setting('review_provider') or SAME_AS_PASS
        if configured == SAME_AS_PASS:
            slot = _detection_slot(db)
        elif configured not in VALID_SLOTS:
            slot = SLOT_PRIMARY
        elif configured == SLOT_SECONDARY and not _secondary_enabled(db):
            slot = SLOT_PRIMARY
        else:
            slot = configured
    else:
        raise ValueError(f"Unknown LLM phase: {stage}")
    # Mirrors _resolve_slot_config's fail-safe: 'secondary' with no provider
    # type configured resolves to primary too.
    if slot == SLOT_SECONDARY and not db.get_setting('secondary_provider'):
        return SLOT_PRIMARY
    return slot


def _detection_model(db) -> str:
    model = db.get_setting('claude_model')
    if model:
        return model
    raise ModelNotConfiguredError('claude_model')


def _verification_model(db) -> str:
    model = db.get_setting('verification_model')
    if model is None:
        return _detection_model(db)
    if not model:
        raise ModelNotConfiguredError('verification_model')
    return model


def _chapters_model(db) -> str:
    model = db.get_setting('chapters_model')
    if model is None:
        return _detection_model(db)
    if not model:
        raise ModelNotConfiguredError('chapters_model')
    return model


def _pattern_cleanup_model(db) -> str:
    model = db.get_setting('pattern_cleanup_model')
    if model is None:
        return _detection_model(db)
    if not model:
        raise ModelNotConfiguredError('pattern_cleanup_model')
    return model


def _resolve_review_route_parts(
        review_provider_setting: str | None, review_model_setting: str | None,
        pass_provider: str | None, pass_model: str | None,
        pass_base_url: str | None, pass_credential_slot: str | None,
        db) -> tuple[str, str, str | None, str]:
    """Resolve review settings; same_as_pass inherits its route, using the standby review model on failover."""
    configured_slot = review_provider_setting or SAME_AS_PASS
    if configured_slot == SAME_AS_PASS:
        if not pass_provider or not pass_model:
            raise ValueError(
                "review phase requires pass_provider and pass_model "
                "when review_provider is same_as_pass")
        credential_slot = pass_credential_slot or SLOT_PRIMARY
        base_url = (pass_base_url if pass_base_url is not None
                    else _base_url_for_primary(pass_provider))
        model = (failover.failover_llm_model('review') if credential_slot == SLOT_FAILOVER
                 else pass_model)
        return pass_provider, model, base_url, credential_slot

    if configured_slot not in VALID_SLOTS:
        configured_slot = SLOT_PRIMARY
    if configured_slot == SLOT_SECONDARY and not _secondary_enabled(db):
        _warn_secondary_fallback_once('review_provider')
        configured_slot = SLOT_PRIMARY
    provider, base_url, credential_slot = _resolve_slot_config(db, configured_slot)

    if review_model_setting == '':
        raise ModelNotConfiguredError('review_model')
    configured_model = review_model_setting
    if configured_model is not None and configured_model != SAME_AS_PASS:
        model = configured_model
    elif pass_model:
        model = pass_model
    else:
        raise ValueError(
            "review phase requires pass_model when review_model is same_as_pass")
    return provider, model, base_url, credential_slot


def resolve_review_route(*, review_provider_setting: str | None,
                          review_model_setting: str | None,
                          pass_provider: str | None,
                          pass_model: str | None,
                          pass_base_url: str | None = None,
                          pass_credential_slot: str | None = None,
                          db=None) -> Route:
    """Review route from explicit setting values, for callers holding a
    frozen run-start gate instead of live settings (see AdReviewer).

    `db` is only consulted to resolve an explicit primary/secondary slot
    (not for review_provider/review_model, which the caller already froze);
    omit it to have this look up a fresh Database() when needed.
    """
    configured_slot = review_provider_setting or SAME_AS_PASS
    if configured_slot != SAME_AS_PASS:
        db = db or database.Database()
    provider, model, base_url, credential_slot = _resolve_review_route_parts(
        review_provider_setting, review_model_setting, pass_provider,
        pass_model, pass_base_url, pass_credential_slot, db)
    return Route(phase='review', provider_key=provider, model_id=model,
                 base_url=base_url, slot=credential_slot,
                 credential_slot=credential_slot,
                 account_id=account_identity(provider, base_url))


def resolve_route(phase: str, *, pass_model: str | None = None,
                   pass_provider: str | None = None,
                   pass_base_url: str | None = None,
                   pass_credential_slot: str | None = None) -> Route:
    """Resolve (provider_key, model_id, base_url, slot, credential_slot)
    for a phase from settings.

    Review honors same_as_pass by inheriting the pass's provider, model,
    base_url, and credential_slot.
    """
    if phase not in PHASES:
        raise ValueError(f"Unknown LLM phase: {phase}")

    db = database.Database()

    if phase == 'detection':
        configured_slot = _detection_slot(db)
        provider, base_url, credential_slot = _resolve_slot_config(db, configured_slot)
        model = _detection_model(db)
    elif phase == 'verification':
        configured_slot = _inherited_slot(db, 'verification_provider', _detection_slot(db))
        provider, base_url, credential_slot = _resolve_slot_config(db, configured_slot)
        model = _verification_model(db)
    elif phase == 'chapters':
        configured_slot = _inherited_slot(db, 'chapters_provider', _detection_slot(db))
        provider, base_url, credential_slot = _resolve_slot_config(db, configured_slot)
        model = _chapters_model(db)
    elif phase == 'pattern_cleanup':
        configured_slot = _inherited_slot(db, 'pattern_cleanup_provider', _detection_slot(db))
        provider, base_url, credential_slot = _resolve_slot_config(db, configured_slot)
        model = _pattern_cleanup_model(db)
    else:  # review
        return resolve_review_route(
            review_provider_setting=db.get_setting('review_provider'),
            review_model_setting=db.get_setting('review_model'),
            pass_provider=pass_provider, pass_model=pass_model,
            pass_base_url=pass_base_url, pass_credential_slot=pass_credential_slot,
            db=db)

    # credential_slot is the actually-resolved slot (post secondary-missing
    # fallback in _resolve_slot_config), which may differ from
    # configured_slot when 'secondary' has no provider type configured.
    return Route(phase=phase, provider_key=provider, model_id=model,
                 base_url=base_url, slot=credential_slot,
                 credential_slot=credential_slot,
                 account_id=account_identity(provider, base_url))
