"""LLM capabilities: per-pass fallback state and provider-aware reasoning translation.

Two responsibilities, intentionally split out of llm_client.py:

1. Fallback flag, keyed by (episode_id, pass_name). When a provider rejects a
   user-configured tunable with a 4xx, the flag for that pass on that episode is
   set, and remaining calls in the same pass use the built-in defaults from this
   module. The flag is cleared explicitly at the start of each pass by the
   orchestrator, so the next pass tries the user's tunables again.

2. Provider translation: map a user-facing reasoning value to the request kwargs
   each provider SDK expects.
"""
import logging
import re
import threading
from dataclasses import dataclass
from typing import Any, Union

from config import (
    PROVIDER_ANTHROPIC,
    PROVIDER_OPENROUTER,
    PROVIDER_OLLAMA,
    PROVIDER_OPENAI_COMPATIBLE,
    PROVIDER_TYPESAFE,
    PROVIDER_SYSTEMONE_COMPATIBLE,
    anthropic_model_allows_disabled_thinking,
    anthropic_model_requires_adaptive_thinking,
    anthropic_model_supports_forced_tools as anthropic_model_supports_forced_tools,
)

logger = logging.getLogger(__name__)

PASS_AD_DETECTION_1 = "ad_detection_pass_1"
PASS_REVIEWER_1 = "reviewer_pass_1"
PASS_AD_DETECTION_2 = "ad_detection_pass_2"
PASS_REVIEWER_2 = "reviewer_pass_2"
PASS_CHAPTER_GENERATION = "chapter_generation"

PassKey = tuple[str, str]


@dataclass(frozen=True)
class PassDefaults:
    temperature: float
    max_tokens: int
    reasoning_effort: Union[int, str] | None = None


# Fallback targets. These match the values used before per-stage tunables existed,
# so a rejection-induced retry restores prior behavior. Do not "improve" these.
_DEFAULTS: dict[str, PassDefaults] = {
    PASS_AD_DETECTION_1: PassDefaults(temperature=0.0, max_tokens=4096),
    PASS_AD_DETECTION_2: PassDefaults(temperature=0.0, max_tokens=4096),
    PASS_REVIEWER_1: PassDefaults(temperature=0.0, max_tokens=4096),
    PASS_REVIEWER_2: PassDefaults(temperature=0.0, max_tokens=4096),
    PASS_CHAPTER_GENERATION: PassDefaults(temperature=0.1, max_tokens=300),
}

_fallback_state: dict[PassKey, bool] = {}
_fallback_lock = threading.Lock()

_REASONING_FIELD = re.compile(
    r'\b(?:reasoning(?:[_ -]?effort)?|thinking|budget[_ -]?tokens)\b')
_REASONING_REQUIRED = (
    re.compile(r'\b(?:reasoning(?:[_ -]?effort)?|thinking)\s+(?:is\s+)?required\b'),
    re.compile(r'\b(?:reasoning|thinking)\s+must\s+be\s+enabled\b'),
    re.compile(r'\b(?:must|need(?:s)?\s+to)\s+(?:enable|use)\s+'
               r'(?:reasoning|thinking)\b'),
    re.compile(r'\b(?:reasoning|thinking)\s+(?:cannot|can\'t|must\s+not)\s+'
               r'be\s+(?:disabled|off)\b'),
)
_REASONING_UNSUPPORTED = (
    re.compile(r'\b(?:reasoning(?:[_ -]?effort)?|thinking)\s+(?:is\s+)?'
               r'(?:unsupported|not\s+supported|unavailable|not\s+available)\b'),
    re.compile(r'\bdoes\s+not\s+support\s+(?:reasoning(?:[_ -]?effort)?|thinking)\b'),
    re.compile(r'\b(?:unknown|unrecognized)\s+(?:parameter|field)\s*[: ]\s*'
               r'(?:reasoning[_ -]?effort|thinking)\b'),
)
_TUNABLE_FIELD = re.compile(
    r'\b(?:max[_ -]?(?:completion[_ -]?)?tokens|temperature|top[_ -]?[pk]|'
    r'reasoning(?:[_ -]?effort)?|thinking|budget[_ -]?tokens)\b')


def set_fallback(episode_id: str, pass_name: str) -> None:
    with _fallback_lock:
        _fallback_state[(str(episode_id), pass_name)] = True


def is_fallback_set(episode_id: str, pass_name: str) -> bool:
    with _fallback_lock:
        return _fallback_state.get((str(episode_id), pass_name), False)


def clear_fallback(episode_id: str, pass_name: str) -> None:
    with _fallback_lock:
        _fallback_state.pop((str(episode_id), pass_name), None)


def get_pass_defaults(pass_name: str) -> PassDefaults:
    try:
        return _DEFAULTS[pass_name]
    except KeyError:
        raise ValueError(f"Unknown pass_name: {pass_name!r}") from None


def classify_reasoning_rejection(error: Exception) -> str | None:
    """Classify a reasoning-related rejection without retaining error text."""
    text = str(error).lower()
    if not _REASONING_FIELD.search(text):
        return None
    if any(pattern.search(text) for pattern in _REASONING_REQUIRED):
        return 'required'
    if any(pattern.search(text) for pattern in _REASONING_UNSUPPORTED):
        return 'unsupported'
    return 'incompatible'


def translate_reasoning_effort(
    provider: str,
    value: Union[int, str] | None,
    model: str | None = None,
) -> dict[str, Any]:
    """Map a per-stage reasoning value to provider-native request kwargs.

    Returns {} when the value should be omitted from the request.
    """
    provider = provider.lower()

    if provider == PROVIDER_ANTHROPIC:
        if value is None:
            return {}
        if anthropic_model_requires_adaptive_thinking(model):
            if isinstance(value, int):
                return {}
            if isinstance(value, str) and value.lower() == 'none':
                if anthropic_model_allows_disabled_thinking(model):
                    return {"thinking": {"type": "disabled"}}
                return {
                    "thinking": {"type": "adaptive"},
                    "output_config": {"effort": "low"},
                }
            if isinstance(value, str) and value.lower() in ("low", "medium", "high"):
                return {
                    "thinking": {"type": "adaptive"},
                    "output_config": {"effort": value.lower()},
                }
            return {}
        if isinstance(value, int):
            return {"thinking": {"type": "enabled", "budget_tokens": value}}
        return {}

    if value is None:
        return {}
    if not isinstance(value, str):
        return {}
    normalized = value.lower()
    if normalized not in ("none", "low", "medium", "high"):
        return {}

    if provider in (PROVIDER_OPENAI_COMPATIBLE, PROVIDER_OLLAMA):
        return {"reasoning_effort": normalized}
    if provider == PROVIDER_OPENROUTER:
        return {"extra_body": {"reasoning": {"effort": normalized}}}
    return {}


# Anthropic's adaptive-thinking generation removed the sampling parameters
# (temperature/top_p/top_k); sending any of them returns a 400. Older models
# still accept them. Extend this tuple when Anthropic ships a new model that
# drops sampling (same manual maintenance as DEFAULT_MODEL_PRICING). Matched as
# substrings so bare IDs ("claude-sonnet-5"), provider-prefixed IDs
# ("anthropic/claude-sonnet-5"), and dated variants all resolve.
_ANTHROPIC_NO_SAMPLING_MODELS = (
    "claude-opus-4-7",
    "claude-opus-4-8",
    "claude-opus-5",
    "claude-sonnet-5",
    "claude-fable-5",
    "claude-mythos-5",
    "claude-haiku-5-5",
)

# Per-process memo of models discovered at runtime to reject temperature
# (keyed by lowercased model id); self-heals model_omits_temperature() for
# models not yet in _ANTHROPIC_NO_SAMPLING_MODELS.
_learned_no_temperature_models: set = set()
_learned_no_temperature_lock = threading.Lock()


def mark_model_omits_temperature(model: str) -> None:
    """Remember, for the life of this process, that ``model`` rejects
    temperature (called after a 400; see is_temperature_rejection_error).
    Later model_omits_temperature() calls return True for this model."""
    if not model:
        return
    with _learned_no_temperature_lock:
        _learned_no_temperature_models.add(model.lower())


def model_omits_temperature(
    model: str | None,
    operator_override: bool = False,
) -> bool:
    """True when temperature must be omitted from the request for ``model``.

    Checked in order, any one sufficient: operator_override (the
    ``omit_temperature`` setting; resolved by the caller since this module
    stays DB-free), the static _ANTHROPIC_NO_SAMPLING_MODELS list, then the
    learned _learned_no_temperature_models memo.
    """
    if operator_override:
        return True
    if not model:
        return False
    m = model.lower()
    with _learned_no_temperature_lock:
        if m in _learned_no_temperature_models:
            return True
    # Trailing (?!\d) guards against a token being a prefix of a longer version,
    # e.g. "claude-opus-4-7" must not match a hypothetical "claude-opus-4-70",
    # and "claude-sonnet-5" must not match "claude-sonnet-50".
    return any(re.search(re.escape(token) + r'(?!\d)', m)
               for token in _ANTHROPIC_NO_SAMPLING_MODELS)


# Anthropic supports structured output through tool schemas. Extend this
# set only after verifying another provider's schema-enforcement contract.
_JSON_SCHEMA_SUPPORTED_PROVIDERS = frozenset({PROVIDER_ANTHROPIC})


# Settings holding a model the pipeline sends JSON calls with. review_model
# may hold the 'same_as_pass' sentinel rather than a model name.
STAGE_MODEL_SETTING_KEYS = (
    'claude_model', 'verification_model', 'review_model', 'chapters_model',
)
SAME_AS_PASS = 'same_as_pass'

_SYSTEMONE_PROXY_MODELS = frozenset({'jev-latest', 'jev-preview', 'typesafe/jev'})
_SYSTEMONE_PHASES = frozenset({'detection', 'verification', 'review'})


def systemone_supported_phases(provider: str, model: str | None) -> frozenset[str] | None:
    """Return Jev-supported phases for explicit System One provider/model IDs."""
    provider = (provider or '').lower()
    if provider in (PROVIDER_TYPESAFE, PROVIDER_SYSTEMONE_COMPATIBLE):
        return _SYSTEMONE_PHASES
    if (model or '').strip().casefold() in _SYSTEMONE_PROXY_MODELS:
        return _SYSTEMONE_PHASES
    return None


def chapters_capability_error(
    db, provider: str | None = None, model: str | None = None,
) -> tuple[str | None, str | None, str | None]:
    """Resolve the effective chapters provider/model (unless given) and check System One support."""
    # Local imports: llm_route and llm_client both import this module.
    from llm_client import get_effective_provider_from_snapshot
    from llm_route import SLOT_SECONDARY, resolved_stage_slot

    if provider is None and model is None:
        slot = resolved_stage_slot(db, 'chapters')
        provider = (db.get_setting('secondary_provider') if slot == SLOT_SECONDARY
                    else get_effective_provider_from_snapshot({'llm_provider': db.get_setting('llm_provider')}))
        model = db.get_setting('chapters_model')
        if model is None:
            model = db.get_setting('claude_model')
    supported = systemone_supported_phases(provider or '', model)
    error = None
    if supported is not None and 'chapters' not in supported:
        error = (f"chapters is unsupported for effective provider {provider!r} and "
                 f"model {model!r}. Use a supported chat provider and model.")
    return provider, model, error


def configured_stage_models(get_setting) -> list[str]:
    """Distinct model names configured across the pipeline stages, in order."""
    names = [get_setting(key) for key in STAGE_MODEL_SETTING_KEYS]
    return list(dict.fromkeys(n for n in names if n and n != SAME_AS_PASS))


def supports_json_schema(provider: str) -> bool:
    """True when ``provider`` has a proven, enforced structured-output path.

    Only gate on this when the call site needs a guarantee the response
    matches a schema (e.g. an enum field) and would rather fall back to
    json_object than risk a false positive on an unverified provider.
    """
    return (provider or '').lower() in _JSON_SCHEMA_SUPPORTED_PROVIDERS


def is_temperature_rejection_error(error: Exception) -> bool:
    """True for a 400 whose body indicates the model rejects ``temperature``
    outright (Anthropic's adaptive-thinking generation). Distinct from
    is_fallback_eligible_error: identifies this specific case so callers can
    retry with temperature omitted rather than defaulted, since a defaulted
    retry 400s identically here.
    """
    status = getattr(error, 'status_code', None)
    if status is None:
        response = getattr(error, 'response', None)
        if response is not None:
            status = getattr(response, 'status_code', None)
    try:
        status_int = int(status)
    except (TypeError, ValueError):
        return False
    if status_int != 400:
        return False
    text = str(error).lower()
    if 'temperature' not in text:
        return False
    return any(marker in text for marker in ('deprecated', 'unsupported', 'not supported'))


def is_fallback_eligible_error(error: Exception) -> bool:
    """True when a provider 4xx identifies a rejected request tunable."""
    status = getattr(error, 'status_code', None)
    if status is None:
        response = getattr(error, 'response', None)
        if response is not None:
            status = getattr(response, 'status_code', None)
    if status is None:
        return False
    try:
        status_int = int(status)
    except (TypeError, ValueError):
        return False
    if status_int in (401, 403, 404, 408, 409, 425, 429):
        return False
    return 400 <= status_int < 500 and bool(_TUNABLE_FIELD.search(str(error).lower()))
