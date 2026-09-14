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
import logging
from dataclasses import dataclass

from config import (
    ModelNotConfiguredError, PROVIDER_ANTHROPIC, PROVIDER_OPENROUTER,
    PROVIDERS_NON_ANTHROPIC, OPENROUTER_BASE_URL, DEFAULT_OPENAI_BASE_URL,
    coerce_bool_setting,
)
from database import Database
from llm_client import (
    get_effective_provider, get_effective_base_url,
    _normalize_base_url_for_provider,
)

logger = logging.getLogger(__name__)

PHASES = ('detection', 'review', 'verification', 'chapters')

SAME_AS_PASS = 'same_as_pass'
SAME_AS_DETECTION = 'same_as_detection'
SLOT_PRIMARY = 'primary'
SLOT_SECONDARY = 'secondary'
VALID_SLOTS = (SLOT_PRIMARY, SLOT_SECONDARY)

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
        return _normalize_base_url_for_provider(provider, get_effective_base_url())
    return None


def _base_url_for_secondary(db, provider: str) -> str | None:
    """Non-secret endpoint for the secondary slot."""
    if provider == PROVIDER_ANTHROPIC:
        return None
    if provider == PROVIDER_OPENROUTER:
        return OPENROUTER_BASE_URL
    if provider in PROVIDERS_NON_ANTHROPIC:
        raw = db.get_setting('secondary_provider_base_url') or DEFAULT_OPENAI_BASE_URL
        return _normalize_base_url_for_provider(provider, raw)
    return None


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
    provider = get_effective_provider()
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
    """
    if stage == 'detection':
        slot = _detection_slot(db)
    elif stage in ('verification', 'chapters'):
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
    return model if model else _detection_model(db)


def _chapters_model(db) -> str:
    model = db.get_setting('chapters_model')
    return model if model else _detection_model(db)


def _resolve_review_route_parts(
        review_provider_setting: str | None, review_model_setting: str | None,
        pass_provider: str | None, pass_model: str | None,
        pass_base_url: str | None, pass_credential_slot: str | None,
        db) -> tuple[str, str, str | None, str]:
    """(provider_key, model_id, base_url, credential_slot) for the review
    phase from explicit review_provider/review_model setting VALUES.

    review_provider_setting is a SLOT (primary/secondary/same_as_pass).
    same_as_pass inherits the calling pass's provider/model/base_url/
    credential_slot verbatim, ignoring review_model entirely. Shared by
    resolve_route's live read and AdReviewer's frozen run-start gate
    (checkpoint 02 task 3 / 02b task 1), so the rule never drifts between
    the two call paths.
    """
    configured_slot = review_provider_setting or SAME_AS_PASS
    if configured_slot == SAME_AS_PASS:
        if not pass_provider or not pass_model:
            raise ValueError(
                "review phase requires pass_provider and pass_model "
                "when review_provider is same_as_pass")
        credential_slot = pass_credential_slot or SLOT_PRIMARY
        base_url = (pass_base_url if pass_base_url is not None
                    else _base_url_for_primary(pass_provider))
        return pass_provider, pass_model, base_url, credential_slot

    if configured_slot not in VALID_SLOTS:
        configured_slot = SLOT_PRIMARY
    if configured_slot == SLOT_SECONDARY and not _secondary_enabled(db):
        _warn_secondary_fallback_once('review_provider')
        configured_slot = SLOT_PRIMARY
    provider, base_url, credential_slot = _resolve_slot_config(db, configured_slot)

    configured_model = review_model_setting or SAME_AS_PASS
    if configured_model != SAME_AS_PASS:
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
        db = db or Database()
    provider, model, base_url, credential_slot = _resolve_review_route_parts(
        review_provider_setting, review_model_setting, pass_provider,
        pass_model, pass_base_url, pass_credential_slot, db)
    return Route(phase='review', provider_key=provider, model_id=model,
                 base_url=base_url, slot=credential_slot,
                 credential_slot=credential_slot)


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

    db = Database()

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
                 base_url=base_url, slot=credential_slot, credential_slot=credential_slot)
