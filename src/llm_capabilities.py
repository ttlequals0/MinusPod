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
from typing import Any, Dict, Optional, Tuple, Union

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

PassKey = Tuple[str, str]


@dataclass(frozen=True)
class PassDefaults:
    temperature: float
    max_tokens: int
    reasoning_effort: Optional[Union[int, str]] = None


# Fallback targets. These match the values used before per-stage tunables existed,
# so a rejection-induced retry restores prior behavior. Do not "improve" these.
_DEFAULTS: Dict[str, PassDefaults] = {
    PASS_AD_DETECTION_1: PassDefaults(temperature=0.0, max_tokens=4096),
    PASS_AD_DETECTION_2: PassDefaults(temperature=0.0, max_tokens=4096),
    PASS_REVIEWER_1: PassDefaults(temperature=0.0, max_tokens=4096),
    PASS_REVIEWER_2: PassDefaults(temperature=0.0, max_tokens=4096),
    PASS_CHAPTER_GENERATION: PassDefaults(temperature=0.1, max_tokens=300),
}

_fallback_state: Dict[PassKey, bool] = {}
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
    value: Optional[Union[int, str]],
) -> Dict[str, Any]:
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

    if provider == PROVIDER_OPENAI_COMPATIBLE:
        return {"reasoning_effort": normalized}
    if provider == PROVIDER_OPENROUTER:
        return {"extra_body": {"reasoning": {"effort": normalized}}}
    if provider == PROVIDER_OLLAMA:
        return {"extra_body": {"options": {"think": normalized != "none"}}}
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

# Per-process memo of models discovered at runtime to reject temperature, keyed
# by lowercased model id. Self-heals model_omits_temperature() for a model not
# (yet) added to _ANTHROPIC_NO_SAMPLING_MODELS above -- mirrors the
# set_fallback/_fallback_state pattern, but keyed by model since the rejection
# is a property of the model, not of a specific (episode_id, pass_name).
_learned_no_temperature_models: set = set()
_learned_no_temperature_lock = threading.Lock()


def mark_model_omits_temperature(model: str) -> None:
    """Remember, for the life of this process, that ``model`` rejects the
    temperature parameter. Called after a provider 400 identifies the
    rejection (see is_temperature_rejection_error); subsequent calls to
    model_omits_temperature() for this model return True without needing a
    _ANTHROPIC_NO_SAMPLING_MODELS entry or a restart."""
    if not model:
        return
    with _learned_no_temperature_lock:
        _learned_no_temperature_models.add(model.lower())


def model_omits_temperature(
    model: Optional[str],
    operator_override: bool = False,
) -> bool:
    """True when temperature must be omitted from the request for ``model``.

    Resolution order (any one of the three is sufficient to omit):
      1. operator_override -- the ``omit_temperature`` global setting. Forces
         omission for EVERY model, regardless of the static list or the
         learned memo below. This module is intentionally DB-free (see the
         module docstring), so the caller resolves the setting and passes
         the result in; do not add a DB read here.
      2. _ANTHROPIC_NO_SAMPLING_MODELS -- static list of models known at
         release time to reject temperature.
      3. _learned_no_temperature_models -- per-process memo populated by
         mark_model_omits_temperature() after a live 400 identifies the
         rejection for a model not (yet) in the static list.
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


# Providers whose request/response contract this codebase has actually
# implemented enforced structured output for (category repair pass, #565
# follow-up, DTNS 5317): Anthropic's Messages API supports forcing a tool
# call via tool_choice, which makes the model emit JSON matching the tool's
# input_schema instead of prose -- AnthropicClient.messages_create
# translates a response_format={"type": "json_schema", ...} request into a
# forced tool call and reassembles the tool's `input` as the response
# content. Every other provider stays unsupported until proven: this app's
# "openai-compatible" and "ollama" providers front arbitrary endpoints (LM
# Studio, vLLM, the Claude Code wrapper, real Ollama) that mostly do not
# implement OpenAI's json_schema strict mode, and OpenRouter fans out to
# hundreds of models with inconsistent support. Getting this wrong means a
# 400 or a silently-ignored schema on a provider that doesn't actually
# support it -- worse than the existing response_format=json_object /
# prompt-injection fallback callers use instead. Extend this set only after
# verifying a specific provider's contract, the same bar as
# _ANTHROPIC_NO_SAMPLING_MODELS above.
_JSON_SCHEMA_SUPPORTED_PROVIDERS = frozenset({PROVIDER_ANTHROPIC})


def supports_json_schema(provider: str) -> bool:
    """True when ``provider`` has a proven, enforced structured-output path.

    Callers that can tolerate the existing response_format=json_object /
    prompt-injection behavior should not gate on this -- it exists for call
    sites that specifically need a guarantee the response matches a schema
    (e.g. an enum field) and would rather fall back to json_object than
    risk a false positive on an unverified provider.
    """
    return (provider or '').lower() in _JSON_SCHEMA_SUPPORTED_PROVIDERS


def is_temperature_rejection_error(error: Exception) -> bool:
    """True for a 400 whose body indicates the model rejects ``temperature``
    outright (Anthropic's adaptive-thinking generation, e.g. the literal
    "`temperature` is deprecated for this model" message). Distinct from
    is_fallback_eligible_error: this identifies the specific
    temperature-unsupported case so callers can retry with temperature
    OMITTED instead of defaulted -- a defaulted retry 400s identically on
    these models, since the rejected value was never the problem.
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
