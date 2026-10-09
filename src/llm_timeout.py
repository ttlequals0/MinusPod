"""Pure timeout resolution shared by provider clients and settings snapshots."""

import math

from config import (
    LLM_TIMEOUT_DEFAULT,
    LLM_TIMEOUT_LOCAL,
    PROVIDER_ANTHROPIC,
    PROVIDER_OPENROUTER,
    PROVIDER_TYPESAFE,
    PROVIDER_SYSTEMONE_COMPATIBLE,
)


_TIMEOUT_KEYS = {
    'primary': 'llm_timeout_seconds',
    'secondary': 'secondary_llm_timeout_seconds',
    'failover': 'failover_llm_timeout_seconds',
}


def get_llm_timeout_from_snapshot(provider_key, credential_slot, snapshot):
    """Resolve a provider timeout without reading mutable settings again."""
    key = _TIMEOUT_KEYS.get(credential_slot, 'llm_timeout_seconds')
    entry = (snapshot or {}).get(key)
    raw = entry.get('value') if isinstance(entry, dict) else entry
    if raw not in (None, ''):
        try:
            value = float(raw)
            if math.isfinite(value) and value > 0:
                return value
        except (TypeError, ValueError):
            pass
    if provider_key in (PROVIDER_TYPESAFE, PROVIDER_SYSTEMONE_COMPATIBLE):
        return 60.0
    if provider_key in (PROVIDER_ANTHROPIC, PROVIDER_OPENROUTER):
        return float(LLM_TIMEOUT_DEFAULT)
    return float(LLM_TIMEOUT_LOCAL)
