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
    PROVIDER_OPENAI_COMPATIBLE,
    PROVIDER_OLLAMA,
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


def translate_reasoning_effort(
    provider: str,
    value: Union[int, str] | None,
) -> dict[str, Any]:
    """Map a per-stage reasoning value to provider-native request kwargs.

    Returns {} when the value should be omitted from the request.
    """
    if value is None:
        return {}

    provider = provider.lower()

    if provider == PROVIDER_ANTHROPIC:
        if isinstance(value, int):
            return {"thinking": {"type": "enabled", "budget_tokens": value}}
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


# Anthropic is the only provider with a proven, enforced structured-output
# path (json_schema response_format forces a tool_choice call in
# AnthropicClient.messages_create, guaranteeing schema-matching output).
# Other providers front arbitrary/inconsistent backends lacking strict JSON
# schema mode; extend this set only after verifying a provider's actual contract.
_JSON_SCHEMA_SUPPORTED_PROVIDERS = frozenset({PROVIDER_ANTHROPIC})


# Settings holding a model the pipeline sends JSON calls with. review_model
# may hold the 'same_as_pass' sentinel rather than a model name.
STAGE_MODEL_SETTING_KEYS = (
    'claude_model', 'verification_model', 'review_model', 'chapters_model',
)
SAME_AS_PASS = 'same_as_pass'


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
    """True for a 4xx (non-429) response, indicating the user's tunables were
    rejected by the provider. False for 429, 5xx, network, timeout -- those go
    through the existing retry path.
    """
    status = getattr(error, 'status_code', None)
    if status is None:
        response = getattr(error, 'response', None)
        if response is not None:
            status = getattr(response, 'status_code', None)
    if status is None:
        return False
    if status == 429:
        return False
    try:
        status_int = int(status)
    except (TypeError, ValueError):
        return False
    # Auth (401/403) and model/resource not-found (404) are not tunable
    # rejections; a retry with default tunables fails identically and would
    # poison the pass via set_fallback. Route them through the normal error path.
    if status_int in (401, 403, 404):
        return False
    return 400 <= status_int < 500
