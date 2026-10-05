"""Provider failover state (#806): which targets currently run on their failover config."""
import json
import logging
import threading
import time

from config import (
    WHISPER_BACKEND_LOCAL, DEFAULT_OPENAI_BASE_URL, coerce_bool_setting,
)
from database import Database
from llm_client import invalidate_provider_cache
from utils.time import utc_now_iso
from webhook_service import fire_failover_event

logger = logging.getLogger('podcast.failover')

TARGET_LLM_PRIMARY = 'llm:primary'
TARGET_LLM_SECONDARY = 'llm:secondary'
TARGET_WHISPER = 'whisper'
TARGETS = (TARGET_LLM_PRIMARY, TARGET_LLM_SECONDARY, TARGET_WHISPER)
API_TARGET_NAMES = {
    'llm-a': TARGET_LLM_PRIMARY, 'llm-b': TARGET_LLM_SECONDARY, 'transcriber': TARGET_WHISPER}
PHASES = ('detection', 'review', 'verification', 'chapters')
_INACTIVE = {'active': False, 'source': None, 'since': None, 'reason': None}
_CACHE_TTL = 5.0
_cache: dict[str, tuple[float, object]] = {}
_lock = threading.Lock()


def invalidate_cache() -> None:
    with _lock:
        _cache.clear()


def _cached(key, loader):
    now = time.monotonic()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < _CACHE_TTL:
            return hit[1]
    value = loader()
    with _lock:
        _cache[key] = (now, value)
    return value


def _setting(key: str) -> str | None:
    return _cached(f'setting:{key}', lambda: Database().get_setting(key))


def llm_target_for_slot(credential_slot: str) -> str | None:
    return {'primary': TARGET_LLM_PRIMARY, 'secondary': TARGET_LLM_SECONDARY}.get(credential_slot)


def state(target: str) -> dict:
    raw = _setting(f'failover_state:{target}')
    if not raw:
        return dict(_INACTIVE)
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return dict(_INACTIVE)
    if not isinstance(data, dict) or not data.get('active'):
        return dict(_INACTIVE)
    return {k: data.get(k) for k in _INACTIVE}


def is_active(target: str) -> bool:
    return state(target)['active']


def all_states() -> dict[str, dict]:
    return {t: state(t) for t in TARGETS}


def failover_llm_config() -> dict:
    return {
        'provider': _setting('failover_llm_provider') or '',
        'base_url': _setting('failover_llm_base_url') or DEFAULT_OPENAI_BASE_URL,
        'timeout': _setting('failover_llm_timeout_seconds'),
        'max_retries': _setting('failover_llm_max_retries'),
        'models': {p: _setting(f'failover_llm_{p}_model') or '' for p in PHASES},
    }


def failover_llm_model(phase: str) -> str:
    models = failover_llm_config()['models']
    return models.get(phase) or models['detection']


def is_configured(target: str) -> bool:
    if target in (TARGET_LLM_PRIMARY, TARGET_LLM_SECONDARY):
        if not coerce_bool_setting(_setting('failover_llm_enabled')):
            return False
        cfg = failover_llm_config()
        return bool(cfg['provider'] and cfg['models']['detection'])
    if target == TARGET_WHISPER:
        if not coerce_bool_setting(_setting('failover_whisper_enabled')):
            return False
        if (_setting('failover_whisper_backend') or 'openai-api') == WHISPER_BACKEND_LOCAL:
            return True
        return bool(_setting('failover_whisper_api_base_url'))
    return False


def _write_state(target: str, data: dict | None) -> None:
    db = Database()
    key = f'failover_state:{target}'
    if data is None:
        db.clear_setting(key)
    else:
        db.set_setting(key, json.dumps(data), is_default=False)
    invalidate_cache()
    invalidate_provider_cache()


def trigger(target: str, reason: str, source: str = 'auto') -> bool:
    if target not in TARGETS:
        raise ValueError(f'unknown failover target: {target}')
    if not is_configured(target):
        logger.info(f"Failover for {target} not configured; not triggering ({reason})")
        return False
    current = state(target)
    if current['active'] and (current['source'] == 'manual' or source != 'manual'):
        return False
    _write_state(target, {
        'active': True, 'source': source, 'since': utc_now_iso(), 'reason': (reason or '')[:500]})
    Database().record_failover_event(target, 'trigger', source, reason)
    logger.warning(f"Failover triggered for {target} ({source}): {reason}")
    fire_failover_event('trigger', target, source, reason)
    return True


def cancel(target: str, source: str = 'manual') -> bool:
    if target not in TARGETS:
        raise ValueError(f'unknown failover target: {target}')
    current = state(target)
    if not current['active']:
        return False
    if current['source'] == 'manual' and source != 'manual':
        return False
    _write_state(target, None)
    Database().record_failover_event(target, 'cancel', source, None)
    logger.info(f"Failover cancelled for {target} ({source})")
    fire_failover_event('cancel', target, source, None)
    return True


def recent_events(limit: int = 50) -> list[dict]:
    return Database().get_failover_events(limit)
