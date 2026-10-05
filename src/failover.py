"""Provider failover state (#806): which targets currently run on their failover config."""
import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import llm_client
import provider_probe
from config import (
    FAILOVER_API_TARGET_NAMES, WHISPER_BACKEND_API, WHISPER_BACKEND_LOCAL,
    DEFAULT_OPENAI_BASE_URL, coerce_bool_setting,
)
from database import Database
from llm_client import invalidate_provider_cache
from utils.time import parse_iso_utc, utc_now_iso
from webhook_service import fire_failover_event

logger = logging.getLogger('podcast.failover')

TARGET_LLM_PRIMARY = 'llm:primary'
TARGET_LLM_SECONDARY = 'llm:secondary'
TARGET_WHISPER = 'whisper'
TARGETS = (TARGET_LLM_PRIMARY, TARGET_LLM_SECONDARY, TARGET_WHISPER)
API_TARGET_NAMES = FAILOVER_API_TARGET_NAMES
PHASES = ('detection', 'review', 'verification', 'chapters')
_INACTIVE = {'active': False, 'source': None, 'since': None, 'reason': None}


def invalidate_cache() -> None:
    llm_client.clear_settings_cache()


def _setting(key: str) -> str | None:
    return llm_client._get_cached_setting(key)


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


def is_configured(target: str, cfg: dict | None = None) -> bool:
    """`cfg` is an already-built failover_llm_config(), to skip a second build."""
    if target in (TARGET_LLM_PRIMARY, TARGET_LLM_SECONDARY):
        if not coerce_bool_setting(_setting('failover_llm_enabled')):
            return False
        cfg = cfg or failover_llm_config()
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
    invalidate_provider_cache()


def trigger(target: str, reason: str, source: str = 'auto') -> bool:
    """Switch `target` to its failover config; never raises past an unknown target."""
    if target not in TARGETS:
        raise ValueError(f'unknown failover target: {target}')
    try:
        # Another worker may have just written this state; never act on a stale read.
        invalidate_cache()
        if not is_configured(target):
            logger.info(f"Failover for {target} not configured; not triggering ({reason})")
            return False
        current = state(target)
        if current['active'] and (current['source'] == 'manual' or source != 'manual'):
            return False
        _write_state(target, {
            'active': True, 'source': source, 'since': utc_now_iso(), 'reason': (reason or '')[:500]})
    except Exception as exc:
        logger.warning(f"Failover trigger for {target} failed: {exc}")
        return False
    logger.warning(f"Failover triggered for {target} ({source}): {reason}")
    _after_change(target, 'trigger', source, reason)
    return True


def cancel(target: str, source: str = 'manual') -> bool:
    """End `target`'s failover; never raises past an unknown target."""
    if target not in TARGETS:
        raise ValueError(f'unknown failover target: {target}')
    try:
        invalidate_cache()
        current = state(target)
        if not current['active']:
            return False
        if current['source'] == 'manual' and source != 'manual':
            return False
        _write_state(target, None)
    except Exception as exc:
        logger.warning(f"Failover cancel for {target} failed: {exc}")
        return False
    logger.info(f"Failover cancelled for {target} ({source})")
    _after_change(target, 'cancel', source, None)
    return True


def _after_change(target: str, action: str, source: str, reason: str | None) -> None:
    """Streak reset, events row and webhook; a failure here leaves the state change in place."""
    try:
        if action == 'trigger':
            _reset_healthy_streak(target)
        Database().record_failover_event(target, action, source, reason)
        fire_failover_event(action, target, source, reason)
    except Exception as exc:
        logger.warning(f"Failover {action} for {target} applied, but recording it failed: {exc}")


def _reset_healthy_streak(target: str) -> None:
    """Recovery must count only probes taken after this trigger."""
    probe = _PROBE_OF[target]
    data = probe_state(probe)
    if data['healthy_streak']:
        Database().set_setting(f'failover_probe:{probe}',
                               json.dumps({**data, 'healthy_streak': 0}), is_default=False)
        invalidate_cache()


def recent_events(limit: int = 50) -> list[dict]:
    return Database().get_failover_events(limit)


# --- Health probing (#806) ------------------------------------------------

PROBE_TARGETS = ('llm:primary', 'llm:secondary', 'llm:failover', 'whisper:active', 'whisper:failover')
PROBE_API_NAMES = {
    'llm:primary': 'llm-a', 'llm:secondary': 'llm-b', 'llm:failover': 'llm-failover',
    'whisper:active': 'transcriber', 'whisper:failover': 'transcriber-failover',
}
AUTO_TRIGGER_FAILURES = 2
_PROBE_DEFAULT = {'reachable': None, 'status': None, 'detail': '', 'checked_at': None,
                  'healthy_streak': 0, 'failed_streak': 0}
_ORIGIN_OF = {'llm:primary': TARGET_LLM_PRIMARY, 'llm:secondary': TARGET_LLM_SECONDARY,
              'whisper:active': TARGET_WHISPER}
_PROBE_OF = {origin: probe for probe, origin in _ORIGIN_OF.items()}
_MAX_PROBE_WORKERS = 5
_fresh_probe_lock = threading.Lock()


def probe_interval_seconds() -> int:
    try:
        return max(1, min(60, int(_setting('failover_probe_interval_minutes') or 5))) * 60
    except (TypeError, ValueError):
        return 300


def recovery_probes() -> int:
    try:
        return max(1, min(10, int(_setting('failover_recovery_probes') or 3)))
    except (TypeError, ValueError):
        return 3


def enabled_probe_targets() -> list[str]:
    targets = ['llm:primary']
    if coerce_bool_setting(_setting('secondary_provider_enabled')):
        targets.append('llm:secondary')
    if coerce_bool_setting(_setting('failover_llm_enabled')) and _setting('failover_llm_provider'):
        targets.append('llm:failover')
    targets.append('whisper:active')
    if coerce_bool_setting(_setting('failover_whisper_enabled')):
        targets.append('whisper:failover')
    return targets


def run_probe_targets() -> list[str]:
    """Probe targets an episode run depends on; a local transcriber is left to the background probe."""
    targets = [t for t in enabled_probe_targets() if t not in ('llm:failover', 'whisper:failover')]
    if (_setting('whisper_backend') or WHISPER_BACKEND_LOCAL) != WHISPER_BACKEND_API:
        targets.remove('whisper:active')
    return targets


def probe_state(target: str) -> dict:
    raw = _setting(f'failover_probe:{target}')
    try:
        data = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        data = {}
    return {**_PROBE_DEFAULT, **{k: data.get(k, v) for k, v in _PROBE_DEFAULT.items()}}


def all_probe_states() -> dict[str, dict]:
    return {t: probe_state(t) for t in PROBE_TARGETS}


def _llm_slot_config(slot: str) -> tuple[str, str | None, str]:
    """(provider, base_url, api_key) for an LLM probe target."""
    if slot == 'failover':
        cfg = failover_llm_config()
        return cfg['provider'], cfg['base_url'], llm_client.get_effective_failover_llm_api_key() or ''
    if slot == 'secondary':
        return (_setting('secondary_provider') or '', _setting('secondary_provider_base_url'),
                llm_client.get_effective_secondary_provider_api_key() or '')
    provider = llm_client.get_effective_provider()
    return provider, llm_client.get_effective_base_url(), llm_client.get_effective_api_key_for(provider) or ''


def _whisper_probe_settings(slot: str) -> dict:
    """The transcriber's own settings for a whisper probe target, env fallbacks included."""
    import transcriber  # inline: importing it loads the local whisper stack
    if slot == 'failover':
        return transcriber._get_failover_whisper_settings()
    return transcriber._get_whisper_settings()


def _probe_local_whisper() -> dict:
    import transcriber  # inline: importing it loads the local whisper stack
    if transcriber.local_transcription_available():
        return {'reachable': True, 'status': None, 'detail': 'Local stack available'}
    return {'reachable': False, 'status': None, 'detail': 'Local whisper stack is not installed'}


def probe_target(target: str) -> dict:
    try:
        kind, which = target.split(':', 1)
        if kind == 'llm':
            provider, base_url, key = _llm_slot_config(which)
            if not provider:
                return {'reachable': None, 'status': None, 'detail': 'Not configured'}
            if provider in provider_probe.FIXED_PROVIDER_PROBES:
                result = provider_probe.probe_fixed_endpoint(provider, key)
            else:
                norm = llm_client._normalize_base_url_for_provider(provider, base_url or DEFAULT_OPENAI_BASE_URL)
                result = provider_probe.probe_models_endpoint(norm, key)
            rejected = (401, 402, 403, 404)
        else:
            settings = _whisper_probe_settings(which)
            if settings['backend'] == WHISPER_BACKEND_LOCAL:
                return _probe_local_whisper()
            if not settings['api_base_url']:
                return {'reachable': None, 'status': None, 'detail': 'Not configured'}
            result = provider_probe.probe_models_endpoint(
                settings['api_base_url'].rstrip('/'), settings['api_key'] or '')
            # Many whisper servers have no /models, so a 404 still proves the server is up.
            rejected = (401, 403)
        status = result.get('status')
        reachable = bool(result.get('reachable')) and status not in rejected and (status is None or status < 500)
        return {'reachable': reachable, 'status': status, 'detail': result.get('detail', '')}
    except Exception as exc:
        logger.debug(f"probe {target} failed: {exc}")
        return {'reachable': False, 'status': None, 'detail': str(exc)[:200]}


def _record_probe(db, target: str, result: dict) -> dict:
    prev = probe_state(target)
    healthy = result['reachable'] is True
    if result['reachable'] is None:
        # Not configured: neither healthy nor failed.
        streaks = {'healthy_streak': 0, 'failed_streak': 0}
    else:
        streaks = {'healthy_streak': prev['healthy_streak'] + 1 if healthy else 0,
                   'failed_streak': 0 if healthy else prev['failed_streak'] + 1}
    data = {**prev, **result, 'checked_at': utc_now_iso(), **streaks}
    db.set_setting(f'failover_probe:{target}', json.dumps(data), is_default=False)
    invalidate_cache()
    return data


def probe_tick(db, targets: list[str] | None = None) -> dict[str, dict]:
    """Probe every enabled target, then apply auto trigger and auto recovery."""
    targets = list(targets or enabled_probe_targets())
    # Probe concurrently so a batch costs one probe timeout, not one per target.
    with ThreadPoolExecutor(max_workers=min(len(targets), _MAX_PROBE_WORKERS)) as exe:
        probed = list(exe.map(probe_target, targets))
    results = {}
    for target, result in zip(targets, probed, strict=True):
        data = _record_probe(db, target, result)
        results[target] = data
        origin = _ORIGIN_OF.get(target)
        if origin is None or data['reachable'] is None:
            continue
        if data['failed_streak'] >= AUTO_TRIGGER_FAILURES and not is_active(origin):
            trigger(origin, f"health probe failed {data['failed_streak']} times: {data['detail']}", source='probe')
        elif (data['healthy_streak'] >= recovery_probes() and is_active(origin)
              and _parse_iso(data['checked_at']) > _parse_iso(state(origin)['since'])):
            cancel(origin, source='auto')
    return results


def _parse_iso(value: str | None) -> float:
    dt = parse_iso_utc(value)
    return dt.timestamp() if dt else 0.0


def ensure_fresh_probes(targets: list[str]) -> None:
    """Probe only targets whose last probe is older than the interval."""
    # Serialized so concurrent episode starts do not re-probe the same stale targets.
    with _fresh_probe_lock:
        cutoff = time.time() - probe_interval_seconds()
        stale = []
        for target in targets:
            checked = probe_state(target)['checked_at']
            if not checked or _parse_iso(checked) < cutoff:
                stale.append(target)
        if stale:
            probe_tick(Database(), stale)
