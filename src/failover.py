"""Provider failover state (#806): which targets currently run on their failover config."""
import json
import hashlib
import logging
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor

import llm_client
import provider_probe
import secrets_crypto
import database
import webhook_service
import transcriber
from config import (
    FAILOVER_API_TARGET_NAMES, HTTP_TIMEOUT_PROBE, WHISPER_BACKEND_API, WHISPER_BACKEND_LOCAL,
    WHISPER_DEVICE_DEFAULT, DEFAULT_OPENAI_BASE_URL, coerce_bool_setting, normalize_whisper_device,
)
from utils.time import parse_iso_utc, utc_now_iso

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
    raw = database.Database().get_setting(f'failover_state:{target}')
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
            return transcriber.local_transcription_available()
        return bool(_setting('failover_whisper_api_base_url'))
    return False


def target_enabled(target: str) -> bool:
    """Whether the original target is in use: Provider B only while enabled with a type."""
    if target == TARGET_LLM_SECONDARY:
        return (coerce_bool_setting(_setting('secondary_provider_enabled'))
                and bool(_setting('secondary_provider')))
    return True


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
        database.Database._upsert_setting(
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
        database.Database._upsert_setting(conn, setting_key, value, is_default=False)
        _reset_healthy_streak_in_transaction(conn, target)
    else:
        if not current['active'] or (current['source'] == 'manual' and source != 'manual'):
            return False
        conn.execute("DELETE FROM settings WHERE key = ?", (setting_key,))
    generation_key = f'failover_generation:{target}'
    database.Database._upsert_setting(
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
    db = database.Database()
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
        llm_client.invalidate_provider_cache()
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
        webhook_service.fire_failover_event(action, target, source, reason)
    except Exception as exc:
        logger.warning(f"Failover {action} for {target} applied, but its webhook failed: {exc}")


def recent_events(limit: int = 50) -> list[dict]:
    return database.Database().get_failover_events(limit)


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
_PROBE_LEASE_SECONDS = 30.0
_PROBE_LEASE_RENEW_SECONDS = 5.0
# An HTTP probe can spend HTTP_TIMEOUT_PROBE on connect and again on read.
_PROBE_WAIT_SECONDS = 2 * HTTP_TIMEOUT_PROBE + 2.0
_PROBE_WAIT_POLL_SECONDS = 0.05
_ANY_CHECKED_AT = object()
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
        ('whisper_backend', 'whisper_api_base_url', 'whisper_api_key',
         'whisper_model', 'whisper_compute_type'),
        ('WHISPER_BACKEND', 'WHISPER_API_BASE_URL', 'WHISPER_API_KEY',
         'WHISPER_MODEL', 'WHISPER_DEVICE', 'WHISPER_COMPUTE_TYPE'),
    ),
    'whisper:failover': (
        ('failover_whisper_enabled', 'failover_whisper_backend',
         'failover_whisper_api_base_url', 'failover_whisper_api_key',
         'failover_whisper_model', 'whisper_model', 'whisper_compute_type'),
        ('WHISPER_MODEL', 'WHISPER_DEVICE', 'WHISPER_COMPUTE_TYPE'),
    ),
}
_PROBE_REQUEST_PREFIX = 'failover_probe_requested:'


def _settings_query(keys) -> str:
    return f"SELECT key, value FROM settings WHERE key IN ({','.join('?' * len(keys))})"  # noqa: S608


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


def run_probe_targets(active_phases: dict | None = None,
                      *, whisper_required: bool = True) -> list[str]:
    targets = []
    if active_phases is None:
        for target in (TARGET_LLM_PRIMARY, TARGET_LLM_SECONDARY):
            if target == TARGET_LLM_SECONDARY and not coerce_bool_setting(
                    _setting('secondary_provider_enabled')):
                continue
            targets.append(
                'llm:failover' if state(target)['active'] and is_configured(target)
                else target
            )
    else:
        slots = {route.get('credential_slot', 'primary')
                 for route in active_phases.values() if isinstance(route, dict)}
        targets.extend(
            target for slot, target in (
                ('primary', 'llm:primary'),
                ('secondary', 'llm:secondary'),
                ('failover', 'llm:failover'),
            ) if slot in slots
        )
    if not whisper_required:
        return targets
    whisper_failover = state(TARGET_WHISPER)['active'] and is_configured(TARGET_WHISPER)
    if whisper_failover:
        failover_enabled = coerce_bool_setting(_setting('failover_whisper_enabled'))
        backend = _setting('failover_whisper_backend') or WHISPER_BACKEND_API
        base_url = _setting('failover_whisper_api_base_url') or ''
        if failover_enabled and backend == WHISPER_BACKEND_API and base_url:
            targets.append('whisper:failover')
    elif (_setting('whisper_backend') or WHISPER_BACKEND_LOCAL) == WHISPER_BACKEND_API:
        targets.append('whisper:active')
    return list(dict.fromkeys(targets))


def probe_state(target: str) -> dict:
    raw = database.Database().get_setting(f'failover_probe:{target}')
    return _public_probe_state(_probe_state_value(raw))


def _probe_config_identity(conn, target: str) -> str:
    setting_keys, _ = _PROBE_CONFIG[target]
    rows = conn.execute(_settings_query(setting_keys), setting_keys).fetchall()
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
    if target == 'whisper:active':
        keys.extend(('failover_state:whisper', 'transcribe_last_local_outcome'))
    rows = conn.execute(_settings_query(keys), keys).fetchall()
    values = {row['key']: row['value'] for row in rows}
    try:
        generation = max(0, int(values.get(f'failover_generation:{origin}') or 0)) if origin else None
    except (TypeError, ValueError):
        generation = 0 if origin else None
    environment = {key: os.environ.get(key) for key in _PROBE_CONFIG[target][1]}
    identity = _probe_config_identity_from_values(target, values, environment)
    request_config = _probe_request_config(db, target, values, environment)
    local_state = {}
    if target == 'whisper:active':
        try:
            local_state = json.loads(values.get('failover_state:whisper') or '{}')
        except (TypeError, ValueError):
            local_state = {}
        request_config['recover_runtime'] = bool(
            isinstance(local_state, dict) and local_state.get('active'))
    return {
        'generation': generation,
        'config_identity': identity,
        'observed_at': _probe_observation_time(),
        'request_config': request_config,
        'local_outcome_stamp': values.get('transcribe_last_local_outcome')
        if target == 'whisper:active' else None,
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
            'local_model': settings.get('whisper_model') or
                           environment.get('WHISPER_MODEL') or 'small',
            **_local_runtime_config(settings, environment),
        }
    return {
        'backend': settings.get('failover_whisper_backend') or WHISPER_BACKEND_API,
        'api_base_url': settings.get('failover_whisper_api_base_url') or '',
        'api_key': _probe_secret(db, settings.get('failover_whisper_api_key')) or '',
        'local_model': settings.get('failover_whisper_model') or
                       settings.get('whisper_model') or
                       environment.get('WHISPER_MODEL') or 'small',
        **_local_runtime_config(settings, environment),
    }


def _local_runtime_config(settings: dict, environment: dict) -> dict:
    """Device and compute type normalized the way the runtime loads them."""
    return {
        'device': normalize_whisper_device(environment.get('WHISPER_DEVICE')) or WHISPER_DEVICE_DEFAULT,
        'compute_type': transcriber.canonical_compute_type(
            settings.get('whisper_compute_type') or environment.get('WHISPER_COMPUTE_TYPE')),
    }


def _local_stamp_changed(old: str | None, new: str | None, model: str | None) -> bool:
    """True when a newer local outcome concerns `model`; standby-model outcomes are ignored."""
    if old == new:
        return False
    try:
        outcome = json.loads(new) if new else None
    except (TypeError, ValueError):
        return True
    return not isinstance(outcome, dict) or outcome.get('model') in (None, model)


def _probe_context_is_current(conn, target: str, context: dict) -> bool:
    origin = _ORIGIN_OF.get(target)
    if origin and _generation_in_transaction(conn, origin) != context['generation']:
        return False
    if target == 'whisper:active' and _local_stamp_changed(
            context.get('local_outcome_stamp'),
            _setting_in_transaction(conn, 'transcribe_last_local_outcome'),
            context.get('request_config', {}).get('local_model')):
        return False
    return _probe_config_identity(conn, target) == context['config_identity']


def all_probe_states() -> dict[str, dict]:
    return {t: probe_state(t) for t in PROBE_TARGETS}


def probe_target(target: str, request_config: dict | None = None) -> dict:
    try:
        if request_config is None:
            request_config = _capture_probe_context(database.Database(), target)['request_config']
        kind, which = target.split(':', 1)
        if kind == 'llm':
            provider = request_config['provider']
            base_url = request_config['base_url']
            key = request_config['api_key']
            if not provider:
                return {'reachable': None, 'status': None, 'detail': 'Not configured'}
            if provider in provider_probe.FIXED_PROVIDER_PROBES:
                if not key:
                    return {'reachable': None, 'status': None, 'detail': 'Not configured'}
                result = provider_probe.probe_fixed_endpoint(provider, key)
            else:
                norm = llm_client._normalize_base_url_for_provider(provider, base_url or DEFAULT_OPENAI_BASE_URL)
                result = provider_probe.probe_models_endpoint(norm, key)
        else:
            settings = request_config
            if settings['backend'] == WHISPER_BACKEND_LOCAL:
                return transcriber.probe_local_transcription(settings)
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
            # Some Whisper servers omit /models (404) or only accept POST there (405).
            reachable = status in (404, 405) or (
                status is not None and 200 <= status < 300
            )
        return {'reachable': reachable, 'status': status, 'detail': result.get('detail', '')}
    except Exception as exc:
        logger.debug(f"probe {target} failed: {exc}")
        return {'reachable': False, 'status': None, 'detail': str(exc)[:200]}


def _probe_state_value(raw: str | None) -> dict:
    try:
        data = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        data = {}
    return {**_PROBE_DEFAULT, **data} if isinstance(data, dict) else dict(_PROBE_DEFAULT)


def _public_probe_state(data: dict) -> dict:
    return {key: data.get(key, default) for key, default in _PROBE_DEFAULT.items()}


def _probe_lease_key(target: str) -> str:
    return f'failover_probe_lease:{target}'


def _claim_probe_lease(db, target: str, *,
                       expected_checked_at=_ANY_CHECKED_AT) -> str | None:
    token = uuid.uuid4().hex
    with db.transaction(immediate=True) as conn:
        now = time.time()
        if expected_checked_at is not _ANY_CHECKED_AT:
            raw_state = _setting_in_transaction(conn, f'failover_probe:{target}')
            if _probe_state_value(raw_state)['checked_at'] != expected_checked_at:
                return None
        raw = _setting_in_transaction(conn, _probe_lease_key(target))
        try:
            lease = json.loads(raw) if raw else {}
        except (TypeError, ValueError):
            lease = {}
        try:
            expires_at = float(lease.get('expires_at', 0))
        except (TypeError, ValueError, AttributeError):
            expires_at = 0
        if expires_at > now:
            return None
        database.Database._upsert_setting(
            conn, _probe_lease_key(target),
            json.dumps({'token': token, 'expires_at': now + _PROBE_LEASE_SECONDS}),
            is_default=False,
        )
    return token


def _release_probe_lease_in_transaction(conn, target: str, token: str) -> None:
    key = _probe_lease_key(target)
    raw = _setting_in_transaction(conn, key)
    try:
        lease = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        lease = {}
    if isinstance(lease, dict) and lease.get('token') == token:
        conn.execute('DELETE FROM settings WHERE key = ?', (key,))


def _release_probe_lease(db, target: str, token: str) -> None:
    with db.transaction(immediate=True) as conn:
        _release_probe_lease_in_transaction(conn, target, token)


def _renew_probe_lease(db, target: str, token: str) -> bool:
    with db.transaction(immediate=True) as conn:
        key = _probe_lease_key(target)
        raw = _setting_in_transaction(conn, key)
        try:
            lease = json.loads(raw) if raw else {}
        except (TypeError, ValueError):
            lease = {}
        if not isinstance(lease, dict) or lease.get('token') != token:
            return False
        now = time.time()
        database.Database._upsert_setting(
            conn, key,
            json.dumps({'token': token, 'expires_at': now + _PROBE_LEASE_SECONDS}),
            is_default=False,
        )
    return True


def _record_probe(db, target: str, result: dict, context: dict) -> tuple[dict, str | None]:
    key = f'failover_probe:{target}'
    origin = _ORIGIN_OF.get(target)
    action = None
    with db.transaction(immediate=True) as conn:
        raw = _setting_in_transaction(conn, key)
        previous = _probe_state_value(raw)
        lease_raw = _setting_in_transaction(conn, _probe_lease_key(target))
        try:
            lease = json.loads(lease_raw) if lease_raw else {}
        except (TypeError, ValueError):
            lease = {}
        if not isinstance(lease, dict) or lease.get('token') != context['lease_token']:
            return _public_probe_state(previous), None
        if not _probe_context_is_current(conn, target, context):
            _release_probe_lease_in_transaction(conn, target, context['lease_token'])
            return _public_probe_state(previous), None
        same_context = (
            previous.get('_generation') == context['generation']
            and previous.get('_config_identity') == context['config_identity']
        )
        healthy_streak = previous.get('healthy_streak', 0) if same_context else 0
        failed_streak = previous.get('failed_streak', 0) if same_context else 0
        local_outcome = result.get('local_outcome')
        local_outcome_stamp = context.get('local_outcome_stamp')
        request_config = context.get('request_config', {})
        if (target == 'whisper:active' and result.get('reachable') is True
                and isinstance(local_outcome, dict)
                and local_outcome.get('outcome') == 'success'
                and local_outcome.get('backend') == WHISPER_BACKEND_LOCAL
                and local_outcome.get('model') == request_config.get('local_model')
                and local_outcome.get('device') == request_config.get('device')
                and local_outcome.get('compute_type') == request_config.get('compute_type')):
            local_outcome_stamp = json.dumps(local_outcome)
            database.Database._upsert_setting(
                conn, 'transcribe_last_local_outcome', local_outcome_stamp,
                is_default=False,
            )
        probe_result = {key: value for key, value in result.items()
                        if key != 'local_outcome'}
        if result['reachable'] is None:
            # Busy or unconfigured: no evidence either way.
            streaks = {'healthy_streak': healthy_streak, 'failed_streak': failed_streak}
        elif result['reachable'] is True:
            streaks = {'healthy_streak': healthy_streak + 1, 'failed_streak': 0}
        else:
            streaks = {'healthy_streak': 0, 'failed_streak': failed_streak + 1}
        data = {
            **{key: previous.get(key, default) for key, default in _PROBE_DEFAULT.items()},
            **probe_result,
            'checked_at': context['observed_at'],
            **streaks,
            '_generation': context['generation'],
            '_config_identity': context['config_identity'],
            '_local_outcome_stamp': local_outcome_stamp,
        }
        database.Database._upsert_setting(conn, key, json.dumps(data), is_default=False)
        _release_probe_lease_in_transaction(conn, target, context['lease_token'])
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
            llm_client.invalidate_provider_cache()
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


def _claim_or_wait(db, target: str, previous_checked_at: str | None,
                   wait_for_inflight: bool) -> str | None:
    deadline = time.monotonic() + _PROBE_WAIT_SECONDS
    while True:
        if wait_for_inflight:
            latest = probe_state(target)['checked_at']
            if latest and latest != previous_checked_at:
                return None
        token = _claim_probe_lease(
            db, target,
            expected_checked_at=(previous_checked_at if wait_for_inflight
                                 else _ANY_CHECKED_AT),
        )
        if token or not wait_for_inflight:
            return token
        latest = probe_state(target)['checked_at']
        if latest and latest != previous_checked_at:
            return None
        if time.monotonic() >= deadline:
            return None
        time.sleep(_PROBE_WAIT_POLL_SECONDS)


def _probe_requested_target(db, target: str, checked_at: str | None,
                            wait_for_inflight: bool) -> dict:
    token = _claim_or_wait(db, target, checked_at, wait_for_inflight)
    if not token:
        return probe_state(target)
    stop_renewal = threading.Event()

    def keep_lease_alive():
        lease_db = database.Database()
        while not stop_renewal.wait(_PROBE_LEASE_RENEW_SECONDS):
            try:
                if not _renew_probe_lease(lease_db, target, token):
                    return
            except Exception:
                logger.debug(f"Probe lease renewal failed for {target}", exc_info=True)
                return

    renewal = threading.Thread(target=keep_lease_alive, daemon=True)
    renewal.start()
    try:
        context = _capture_probe_context(db, target)
        context['lease_token'] = token
        result = probe_target(target, context['request_config'])
        if result.get('deferred'):
            _release_probe_lease(db, target, token)
            db.set_setting(f'{_PROBE_REQUEST_PREFIX}{target}', utc_now_iso())
            return probe_state(target)
        data, _ = _record_probe(db, target, result, context)
        return data
    except Exception as exc:
        try:
            _release_probe_lease(db, target, token)
        except Exception:
            logger.debug(f"Probe lease release failed for {target}", exc_info=True)
        logger.debug(f"Probe setup failed for {target}: {exc}")
        return probe_state(target)
    finally:
        stop_renewal.set()
        renewal.join(timeout=1)


def probe_tick(db, targets: list[str] | None = None,
               *, wait_for_inflight: bool = False) -> dict[str, dict]:
    targets = list(dict.fromkeys(enabled_probe_targets() if targets is None else targets))
    if not targets:
        return {}
    baselines = {target: probe_state(target)['checked_at'] for target in targets}
    with ThreadPoolExecutor(max_workers=min(len(targets), _MAX_PROBE_WORKERS)) as executor:
        results = executor.map(
            lambda target: _probe_requested_target(
                db, target, baselines[target], wait_for_inflight), targets)
        return dict(zip(targets, results, strict=True))


def take_requested_probes(db) -> list[str]:
    """Pop the probes a web worker handed to the background leader."""
    keys = [f'{_PROBE_REQUEST_PREFIX}{target}' for target in PROBE_TARGETS]
    query = _settings_query(keys)
    if not db.get_connection().execute(query, keys).fetchall():
        return []
    with db.transaction(immediate=True) as conn:
        found = {row['key'] for row in conn.execute(query, keys).fetchall()}
        conn.executemany('DELETE FROM settings WHERE key = ?', [(key,) for key in found])
    return [target for target, key in zip(PROBE_TARGETS, keys, strict=True) if key in found]


def _parse_iso(value: str | None) -> float:
    dt = parse_iso_utc(value)
    return dt.timestamp() if dt else 0.0


def _probe_matches_context(cached: dict, context: dict) -> bool:
    return (
        cached.get('_generation') == context['generation']
        and cached.get('_config_identity') == context['config_identity']
        and not _local_stamp_changed(
            cached.get('_local_outcome_stamp'), context.get('local_outcome_stamp'),
            context.get('request_config', {}).get('local_model'))
    )


def current_probe_state(target: str) -> dict:
    """Return a probe verdict only while its age and captured config remain current."""
    db = database.Database()
    cached = _probe_state_value(db.get_setting(f'failover_probe:{target}'))
    context = _capture_probe_context(db, target)
    checked = cached.get('checked_at')
    current = (
        checked
        and _parse_iso(checked) >= time.time() - probe_interval_seconds()
        and _probe_matches_context(cached, context)
    )
    result = _public_probe_state(cached)
    if not current:
        result['reachable'] = None
    return result


def ensure_fresh_probes(targets: list[str]) -> None:
    cutoff = time.time() - probe_interval_seconds()
    db = database.Database()
    baselines = []
    for target in dict.fromkeys(targets):
        cached = _probe_state_value(db.get_setting(f'failover_probe:{target}'))
        context = _capture_probe_context(db, target)
        checked = cached.get('checked_at')
        if (not checked or _parse_iso(checked) < cutoff
                or not _probe_matches_context(cached, context)):
            baselines.append((target, checked))
    if not baselines:
        return
    with ThreadPoolExecutor(max_workers=min(len(baselines), _MAX_PROBE_WORKERS)) as executor:
        list(executor.map(
            lambda item: _probe_requested_target(db, item[0], item[1], True), baselines))
