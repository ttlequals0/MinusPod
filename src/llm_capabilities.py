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
        raise ValueError(f"Unknown pass_name: {pass_name!r}")


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
    return 400 <= status_int < 500
