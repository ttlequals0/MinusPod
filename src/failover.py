"""Provider failover state (#806): which targets currently run on their failover config."""
import json
import hashlib
import logging
import os
import threading
import time
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor

import llm_client
import provider_probe
import secrets_crypto
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
    raw = Database().get_setting(f'failover_state:{target}')
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
            import transcriber  # inline: importing it loads the local whisper stack
            return transcriber.local_transcription_available()
        return bool(_setting('failover_whisper_api_base_url'))
    return False


class FailoverTransitionError(RuntimeError):
    """A failover state change could not be persisted."""


def _setting_in_transaction(conn, key: str) -> str | None:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row['value'] if row else None


def _configured_in_transaction(conn, target: str) -> bool:
    if target in (TARGET_LLM_PRIMARY, TARGET_LLM_SECONDARY):
        return (
            coerce_bool_setting(_setting_in_transaction(conn, 'failover_llm_enabled'))
            and bool(_setting_in_transaction(conn, 'failover_llm_provider'))
            and bool(_setting_in_transaction(conn, 'failover_llm_detection_model'))
        )
    if not coerce_bool_setting(_setting_in_transaction(conn, 'failover_whisper_enabled')):
        return False
    backend = _setting_in_transaction(conn, 'failover_whisper_backend') or WHISPER_BACKEND_API
    if backend == WHISPER_BACKEND_LOCAL:
        import transcriber
        return transcriber.local_transcription_available()
    return bool(_setting_in_transaction(conn, 'failover_whisper_api_base_url'))


def _state_in_transaction(conn, target: str) -> dict:
    raw = _setting_in_transaction(conn, f'failover_state:{target}')
    try:
        data = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        data = {}
    if not isinstance(data, dict) or not data.get('active'):
        return dict(_INACTIVE)
    return {key: data.get(key) for key in _INACTIVE}


def _generation_in_transaction(conn, target: str) -> int:
    raw = _setting_in_transaction(conn, f'failover_generation:{target}')
    try:
        return max(0, int(raw or 0))
    except (TypeError, ValueError):
        return 0


def _reset_healthy_streak_in_transaction(conn, target: str) -> None:
    probe = _PROBE_OF.get(target)
    if probe is None:
        return
    key = f'failover_probe:{probe}'
    raw = _setting_in_transaction(conn, key)
    try:
        data = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        data = {}
    if isinstance(data, dict) and data.get('healthy_streak'):
        Database._upsert_setting(
            conn, key, json.dumps({**data, 'healthy_streak': 0}), is_default=False)


def _apply_transition_in_transaction(conn, target: str, action: str,
                                     source: str, reason: str | None) -> bool:
    setting_key = f'failover_state:{target}'
    if action == 'trigger' and not _configured_in_transaction(conn, target):
        return False
    current = _state_in_transaction(conn, target)
    if action == 'trigger':
        if current['active'] and (current['source'] == 'manual' or source != 'manual'):
            return False
        value = json.dumps({
            'active': True, 'source': source, 'since': utc_now_iso(),
            'reason': (reason or '')[:500],
        })
        Database._upsert_setting(conn, setting_key, value, is_default=False)
        _reset_healthy_streak_in_transaction(conn, target)
    else:
        if not current['active'] or (current['source'] == 'manual' and source != 'manual'):
            return False
        conn.execute("DELETE FROM settings WHERE key = ?", (setting_key,))
    generation_key = f'failover_generation:{target}'
    Database._upsert_setting(
        conn, generation_key, str(_generation_in_transaction(conn, target) + 1),
        is_default=False,
    )
    conn.execute(
        "INSERT INTO failover_events (target, action, source, reason) VALUES (?, ?, ?, ?)",
        (target, action, source, (reason or '')[:500]),
    )
    return True


def _apply_transition(target: str, action: str, source: str,
                      reason: str | None) -> bool:
    db = Database()
    with db.transaction(immediate=True) as conn:
        return _apply_transition_in_transaction(conn, target, action, source, reason)


def _transition(target: str, action: str, source: str, reason: str | None,
                *, raise_on_error: bool) -> bool:
    if target not in TARGETS:
        raise ValueError(f'unknown failover target: {target}')
    try:
        changed = _apply_transition(target, action, source, reason)
    except Exception as exc:
        invalidate_cache()
        logger.warning(f"Failover {action} for {target} failed: {exc}")
        if raise_on_error:
            raise FailoverTransitionError(
                f"Failover {action} for {target} could not be persisted") from exc
        return False
    invalidate_cache()
    if not changed:
        if action == 'trigger':
            logger.info(f"Failover for {target} not changed ({reason})")
        return False
    try:
        invalidate_provider_cache()
    except Exception as exc:
        logger.warning(f"Failover {action} for {target} applied, but cache invalidation failed: {exc}")
    if action == 'trigger':
        logger.warning(f"Failover triggered for {target} ({source}): {reason}")
    else:
        logger.info(f"Failover cancelled for {target} ({source})")
    _after_change(target, action, source, reason)
    return True


def trigger(target: str, reason: str, source: str = 'auto', *,
            raise_on_error: bool = False) -> bool:
    """Switch `target` to its failover config; optionally raise on write failure."""
    return _transition(target, 'trigger', source, reason, raise_on_error=raise_on_error)


def cancel(target: str, source: str = 'manual', *,
           raise_on_error: bool = False) -> bool:
    """End `target`'s failover; optionally raise on write failure."""
    return _transition(target, 'cancel', source, None, raise_on_error=raise_on_error)


def _after_change(target: str, action: str, source: str, reason: str | None) -> None:
    """Send the webhook after the state and event commit."""
    try:
        fire_failover_event(action, target, source, reason)
    except Exception as exc:
        logger.warning(f"Failover {action} for {target} applied, but its webhook failed: {exc}")


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
_PROBE_CONFIG = {
    'llm:primary': (
        ('llm_provider', 'openai_base_url', 'anthropic_api_key', 'openai_api_key',
         'openrouter_api_key', 'ollama_api_key', 'provider_config_revision'),
        ('LLM_PROVIDER', 'OPENAI_BASE_URL', 'ANTHROPIC_API_KEY', 'OPENAI_API_KEY',
         'OPENROUTER_API_KEY', 'OLLAMA_API_KEY'),
    ),
    'llm:secondary': (
        ('secondary_provider_enabled', 'secondary_provider', 'secondary_provider_base_url',
         'secondary_provider_api_key', 'provider_config_revision'),
        (),
    ),
    'llm:failover': (
        ('failover_llm_enabled', 'failover_llm_provider', 'failover_llm_base_url',
         'failover_llm_api_key', 'provider_config_revision'),
        (),
    ),
    'whisper:active': (
        ('whisper_backend', 'whisper_api_base_url', 'whisper_api_key'),
        ('WHISPER_BACKEND', 'WHISPER_API_BASE_URL', 'WHISPER_API_KEY'),
    ),
    'whisper:failover': (
        ('failover_whisper_enabled', 'failover_whisper_backend',
         'failover_whisper_api_base_url', 'failover_whisper_api_key'),
        (),
    ),
}
_PROBE_CONFIG_QUERY = {
    'llm:primary': "SELECT key, value FROM settings WHERE key IN (?,?,?,?,?,?,?)",
    'llm:secondary': "SELECT key, value FROM settings WHERE key IN (?,?,?,?,?)",
    'llm:failover': "SELECT key, value FROM settings WHERE key IN (?,?,?,?,?)",
    'whisper:active': "SELECT key, value FROM settings WHERE key IN (?,?,?)",
    'whisper:failover': "SELECT key, value FROM settings WHERE key IN (?,?,?,?)",
}
_PROBE_CONTEXT_QUERY = {
    'llm:primary': "SELECT key, value FROM settings WHERE key IN (?,?,?,?,?,?,?,?)",
    'llm:secondary': "SELECT key, value FROM settings WHERE key IN (?,?,?,?,?,?)",
    'whisper:active': "SELECT key, value FROM settings WHERE key IN (?,?,?,?)",
}


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


def _probe_config_identity(conn, target: str) -> str:
    setting_keys, _ = _PROBE_CONFIG[target]
    rows = conn.execute(_PROBE_CONFIG_QUERY[target], setting_keys).fetchall()
    settings = {row['key']: row['value'] for row in rows}
    return _probe_config_identity_from_values(target, settings)


def _probe_observation_time() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')


def _capture_probe_context(db, target: str) -> dict:
    origin = _ORIGIN_OF.get(target)
    conn = db.get_connection()
    setting_keys = _PROBE_CONFIG[target][0]
    keys = list(setting_keys)
    if origin:
        keys.append(f'failover_generation:{origin}')
    query = _PROBE_CONTEXT_QUERY.get(target, _PROBE_CONFIG_QUERY[target])
    rows = conn.execute(query, keys).fetchall()
    values = {row['key']: row['value'] for row in rows}
    try:
        generation = max(0, int(values.get(f'failover_generation:{origin}') or 0)) if origin else None
    except (TypeError, ValueError):
        generation = 0 if origin else None
    environment = {key: os.environ.get(key) for key in _PROBE_CONFIG[target][1]}
    identity = _probe_config_identity_from_values(target, values, environment)
    return {
        'generation': generation,
        'config_identity': identity,
        'observed_at': _probe_observation_time(),
        'request_config': _probe_request_config(db, target, values, environment),
    }


def _probe_config_identity_from_values(target: str, settings: dict[str, str],
                                       environment: dict[str, str | None] | None = None) -> str:
    setting_keys, env_keys = _PROBE_CONFIG[target]
    payload = {
        'settings': {key: settings.get(key) for key in setting_keys},
        'environment': environment or {key: os.environ.get(key) for key in env_keys},
    }
    raw = json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()
    return hashlib.sha256(raw).hexdigest()


def _probe_secret(db, raw: str | None) -> str | None:
    if not raw:
        return None
    try:
        return secrets_crypto.decrypt(db, raw) if secrets_crypto.is_ciphertext(raw) else raw
    except Exception:
        return None


def _probe_request_config(db, target: str, settings: dict[str, str],
                          environment: dict[str, str | None]) -> dict:
    if target.startswith('llm:'):
        slot = target.split(':', 1)[1]
        if slot == 'primary':
            provider = (settings.get('llm_provider') or
                        environment.get('LLM_PROVIDER') or llm_client.PROVIDER_ANTHROPIC).lower()
            key_name = {
                llm_client.PROVIDER_ANTHROPIC: 'anthropic_api_key',
                llm_client.PROVIDER_OPENROUTER: 'openrouter_api_key',
                llm_client.PROVIDER_OLLAMA: 'ollama_api_key',
            }.get(provider, 'openai_api_key')
            env_name = {
                'anthropic_api_key': 'ANTHROPIC_API_KEY',
                'openrouter_api_key': 'OPENROUTER_API_KEY',
                'ollama_api_key': 'OLLAMA_API_KEY',
                'openai_api_key': 'OPENAI_API_KEY',
            }[key_name]
            key = _probe_secret(db, settings.get(key_name)) or environment.get(env_name)
            if key_name in ('openai_api_key', 'ollama_api_key') and not key:
                key = 'not-needed'
            return {
                'provider': provider,
                'base_url': settings.get('openai_base_url') or
                            environment.get('OPENAI_BASE_URL') or DEFAULT_OPENAI_BASE_URL,
                'api_key': key or '',
            }
        if slot == 'secondary':
            return {
                'provider': settings.get('secondary_provider') or '',
                'base_url': settings.get('secondary_provider_base_url'),
                'api_key': _probe_secret(db, settings.get('secondary_provider_api_key')) or '',
            }
        return {
            'provider': settings.get('failover_llm_provider') or '',
            'base_url': settings.get('failover_llm_base_url') or DEFAULT_OPENAI_BASE_URL,
            'api_key': _probe_secret(db, settings.get('failover_llm_api_key')) or '',
        }
    if target == 'whisper:active':
        return {
            'backend': settings.get('whisper_backend') or
                       environment.get('WHISPER_BACKEND') or WHISPER_BACKEND_LOCAL,
            'api_base_url': settings.get('whisper_api_base_url') or
                            environment.get('WHISPER_API_BASE_URL') or '',
            'api_key': _probe_secret(db, settings.get('whisper_api_key')) or
                       environment.get('WHISPER_API_KEY') or '',
        }
    return {
        'backend': settings.get('failover_whisper_backend') or WHISPER_BACKEND_API,
        'api_base_url': settings.get('failover_whisper_api_base_url') or '',
        'api_key': _probe_secret(db, settings.get('failover_whisper_api_key')) or '',
    }


def _probe_context_is_current(conn, target: str, context: dict) -> bool:
    origin = _ORIGIN_OF.get(target)
    if origin and _generation_in_transaction(conn, origin) != context['generation']:
        return False
    return _probe_config_identity(conn, target) == context['config_identity']


def all_probe_states() -> dict[str, dict]:
    return {t: probe_state(t) for t in PROBE_TARGETS}


def _probe_local_whisper() -> dict:
    import transcriber  # inline: importing it loads the local whisper stack
    if transcriber.local_transcription_available():
        return {'reachable': True, 'status': None, 'detail': 'Local stack available'}
    return {'reachable': False, 'status': None, 'detail': 'Local whisper stack is not installed'}


def probe_target(target: str, request_config: dict | None = None) -> dict:
    try:
        if request_config is None:
            request_config = _capture_probe_context(Database(), target)['request_config']
        kind, which = target.split(':', 1)
        if kind == 'llm':
            provider = request_config['provider']
            base_url = request_config['base_url']
            key = request_config['api_key']
            if not provider:
                return {'reachable': None, 'status': None, 'detail': 'Not configured'}
            if provider in provider_probe.FIXED_PROVIDER_PROBES:
                result = provider_probe.probe_fixed_endpoint(provider, key)
            else:
                norm = llm_client._normalize_base_url_for_provider(provider, base_url or DEFAULT_OPENAI_BASE_URL)
                result = provider_probe.probe_models_endpoint(norm, key)
        else:
            settings = request_config
            if settings['backend'] == WHISPER_BACKEND_LOCAL:
                return _probe_local_whisper()
            if not settings['api_base_url']:
                return {'reachable': None, 'status': None, 'detail': 'Not configured'}
            result = provider_probe.probe_models_endpoint(
                settings['api_base_url'].rstrip('/'), settings['api_key'] or '')
        status = result.get('status')
        # No HTTP status at all means run_probe never got a real response
        # (connect/DNS failure or a read timeout, which reports reachable
        # True with no status); never classify that as reachable.
        if kind == 'llm':
            reachable = result.get('ok') is True and status is not None and 200 <= status < 300
        else:
            # Some Whisper servers omit /models, so only their 404 is exceptional.
            reachable = status == 404 or (
                status is not None and 200 <= status < 300
            )
        return {'reachable': reachable, 'status': status, 'detail': result.get('detail', '')}
    except Exception as exc:
        logger.debug(f"probe {target} failed: {exc}")
        return {'reachable': False, 'status': None, 'detail': str(exc)[:200]}


def _record_probe(db, target: str, result: dict, context: dict) -> tuple[dict, str | None]:
    key = f'failover_probe:{target}'
    origin = _ORIGIN_OF.get(target)
    action = None
    with db.transaction(immediate=True) as conn:
        raw = _setting_in_transaction(conn, key)
        try:
            previous = json.loads(raw) if raw else {}
        except (TypeError, ValueError):
            previous = {}
        if not isinstance(previous, dict):
            previous = {}
        if not _probe_context_is_current(conn, target, context):
            return {k: previous.get(k, default)
                    for k, default in _PROBE_DEFAULT.items()}, None
        same_context = (
            previous.get('_generation') == context['generation']
            and previous.get('_config_identity') == context['config_identity']
        )
        healthy_streak = previous.get('healthy_streak', 0) if same_context else 0
        failed_streak = previous.get('failed_streak', 0) if same_context else 0
        if result['reachable'] is None:
            streaks = {'healthy_streak': 0, 'failed_streak': 0}
        elif result['reachable'] is True:
            streaks = {'healthy_streak': healthy_streak + 1, 'failed_streak': 0}
        else:
            streaks = {'healthy_streak': 0, 'failed_streak': failed_streak + 1}
        data = {
            **{key: previous.get(key, default) for key, default in _PROBE_DEFAULT.items()},
            **result,
            'checked_at': context['observed_at'],
            **streaks,
            '_generation': context['generation'],
            '_config_identity': context['config_identity'],
        }
        Database._upsert_setting(conn, key, json.dumps(data), is_default=False)
        if origin and result['reachable'] is not None:
            current = _state_in_transaction(conn, origin)
            if data['failed_streak'] >= AUTO_TRIGGER_FAILURES and not current['active']:
                changed = _apply_transition_in_transaction(
                    conn, origin, 'trigger', 'probe',
                    f"health probe failed {data['failed_streak']} times: {data['detail']}",
                )
                if changed:
                    action = 'trigger'
            elif (data['healthy_streak'] >= _recovery_probes_in_transaction(conn)
                  and current['active'] and current['source'] != 'manual'
                  and _parse_iso(context['observed_at']) > _parse_iso(current['since'])):
                changed = _apply_transition_in_transaction(conn, origin, 'cancel', 'auto', None)
                if changed:
                    action = 'cancel'
    invalidate_cache()
    if action:
        try:
            invalidate_provider_cache()
        except Exception as exc:
            logger.warning(f"Failover {action} for {origin} applied, but cache invalidation failed: {exc}")
        if action == 'trigger':
            logger.warning(f"Failover triggered for {origin} (probe): {data['detail']}")
        else:
            logger.info(f"Failover cancelled for {origin} (auto)")
        _after_change(origin, action, 'probe' if action == 'trigger' else 'auto',
                      data['detail'] if action == 'trigger' else None)
    return {k: data[k] for k in _PROBE_DEFAULT}, action


def _recovery_probes_in_transaction(conn) -> int:
    try:
        raw = _setting_in_transaction(conn, 'failover_recovery_probes')
        return max(1, min(10, int(raw or 3)))
    except (TypeError, ValueError):
        return 3


def probe_tick(db, targets: list[str] | None = None) -> dict[str, dict]:
    """Probe every enabled target, then apply auto trigger and auto recovery."""
    targets = list(targets or enabled_probe_targets())
    def run(target):
        context = _capture_probe_context(db, target)
        return context, probe_target(target, context['request_config'])

    # Probe concurrently so a batch costs one probe timeout, not one per target.
    with ThreadPoolExecutor(max_workers=min(len(targets), _MAX_PROBE_WORKERS)) as exe:
        probed = list(exe.map(run, targets))
    results = {}
    for target, (context, result) in zip(targets, probed, strict=True):
        data, _ = _record_probe(db, target, result, context)
        results[target] = data
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
