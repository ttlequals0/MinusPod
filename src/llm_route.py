"""Per-phase LLM route resolution: (provider, model, base_url) per pipeline
phase, so detection/verification/review/chapters can each target a
different provider. Credentials are resolved separately at call time by
provider_key; Route never carries a secret.
"""
from dataclasses import dataclass

from config import (
    ModelNotConfiguredError, PROVIDER_ANTHROPIC, PROVIDER_OPENROUTER,
    PROVIDERS_NON_ANTHROPIC, OPENROUTER_BASE_URL,
)
from database import Database
from llm_client import (
    get_effective_provider, get_effective_base_url,
    _normalize_base_url_for_provider,
)

PHASES = ('detection', 'review', 'verification', 'chapters')

SAME_AS_PASS = 'same_as_pass'


@dataclass(frozen=True)
class Route:
    phase: str
    provider_key: str
    model_id: str
    base_url: str | None  # non-secret; never includes embedded credentials


def _base_url_for_provider(provider: str) -> str | None:
    """Non-secret endpoint for a provider, mirroring llm_client._build_client."""
    if provider == PROVIDER_ANTHROPIC:
        return None
    if provider == PROVIDER_OPENROUTER:
        return OPENROUTER_BASE_URL
    if provider in PROVIDERS_NON_ANTHROPIC:
        return _normalize_base_url_for_provider(provider, get_effective_base_url())
    return None


def _detection_provider(db) -> str:
    return db.get_setting('detection_provider') or get_effective_provider()


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


def _review_model(db, pass_model: str | None) -> str:
    configured = db.get_setting('review_model') or SAME_AS_PASS
    if configured != SAME_AS_PASS:
        return configured
    if not pass_model:
        raise ValueError(
            "review phase requires pass_model when review_model is same_as_pass")
    return pass_model


def resolve_route(phase: str, *, pass_model: str | None = None,
                   pass_provider: str | None = None) -> Route:
    """Resolve (provider_key, model_id, base_url) for a phase from settings.

    Review honors same_as_pass by inheriting BOTH pass_provider and pass_model.
    """
    if phase not in PHASES:
        raise ValueError(f"Unknown LLM phase: {phase}")

    db = Database()

    if phase == 'detection':
        provider = _detection_provider(db)
        model = _detection_model(db)
    elif phase == 'verification':
        provider = db.get_setting('verification_provider') or _detection_provider(db)
        model = _verification_model(db)
    elif phase == 'chapters':
        provider = db.get_setting('chapters_provider') or _detection_provider(db)
        model = _chapters_model(db)
    else:  # review
        configured_provider = db.get_setting('review_provider') or SAME_AS_PASS
        if configured_provider == SAME_AS_PASS:
            if not pass_provider or not pass_model:
                raise ValueError(
                    "review phase requires pass_provider and pass_model "
                    "when review_provider is same_as_pass")
            provider = pass_provider
            model = pass_model
        else:
            provider = configured_provider
            model = _review_model(db, pass_model)

    return Route(phase=phase, provider_key=provider, model_id=model,
                 base_url=_base_url_for_provider(provider))
