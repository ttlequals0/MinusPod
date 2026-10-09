"""Provider API key management: /settings/providers/*

Stores LLM/Whisper credentials encrypted at rest. GET never returns key
values (booleans + source only). All outbound base URLs pass SSRF validation.
"""
import logging
import os

import requests
from flask import request

import failover
import transcriber
from api import api, error_response, json_response, limiter
from api.settings import (
    SLOT_ALIASES, _clear_models_for_identity_changes,
    _model_identity_snapshot, calibration_revision,
    finish_settings_payload_after_commit,
)
from config import (
    DEFAULT_OPENAI_BASE_URL, HTTP_MAX_REDIRECTS_API, HTTP_TIMEOUT_PROBE,
    PROVIDER_ANTHROPIC, PROVIDER_OLLAMA, PROVIDER_OPENAI_COMPATIBLE,
    PROVIDER_OPENROUTER,
)
from database import Database
from llm_client import (
    get_effective_base_url, get_effective_failover_llm_api_key,
    get_effective_secondary_provider_api_key, _normalize_base_url_for_provider,
)
from llm_route import (
    SLOT_PRIMARY, VALID_SLOTS, account_identity_for_primary_provider,
    account_identity_for_slot,
)
from provider_probe import (
    FIXED_PROVIDER_PROBES as _FIXED_PROVIDER_PROBES,
    models_request as _models_request,
    probe_fixed_endpoint as _probe_fixed_endpoint,
    probe_models_endpoint as _probe_models_endpoint,
    same_server as _same_server,
)
from rate_limit_hold import clear_hold_for_provider_change
from secrets_crypto import is_available as crypto_available
from utils.safe_http import URLTrust, safe_get
from utils.secret_writes import SecretWriteRejected, set_or_clear_secret
from utils.url import (
    BASE_URL_USERINFO_ERROR, SSRFError, url_has_userinfo, validate_base_url,
)

logger = logging.getLogger(__name__)

_PROVIDERS = {
    'anthropic':  {'secret': 'anthropic_api_key',  'base_url': None,                  'base_env': None,                 'model': None,                'env': 'ANTHROPIC_API_KEY'},
    'openai':     {'secret': 'openai_api_key',     'base_url': 'openai_base_url',     'base_env': 'OPENAI_BASE_URL',    'model': None,                'env': 'OPENAI_API_KEY'},
    'openrouter': {'secret': 'openrouter_api_key', 'base_url': None,                  'base_env': None,                 'model': None,                'env': 'OPENROUTER_API_KEY'},
    'whisper':    {'secret': 'whisper_api_key',    'base_url': 'whisper_api_base_url','base_env': 'WHISPER_API_BASE_URL','model': 'whisper_api_model', 'env': 'WHISPER_API_KEY'},
    'ollama':     {'secret': 'ollama_api_key',     'base_url': 'openai_base_url',     'base_env': 'OPENAI_BASE_URL',    'model': None,                'env': 'OLLAMA_API_KEY'},
}

# Providers whose key or endpoint feeds the LLM client, so a write here can
# invalidate a rate-limit hold. Whisper is a separate service (#696).
_LLM_PROVIDERS = ('anthropic', 'openai', 'openrouter', 'ollama')

# This endpoint's provider names ('openai') differ from the internal
# provider_key hold markers are keyed by ('openai-compatible'); map to the
# real key so clearing a hold here targets the marker that was actually set.
_HOLD_PROVIDER_KEY = {
    'anthropic': PROVIDER_ANTHROPIC,
    'openai': PROVIDER_OPENAI_COMPATIBLE,
    'openrouter': PROVIDER_OPENROUTER,
    'ollama': PROVIDER_OLLAMA,
}


# What a save may do to runs frozen against the account it replaces.
_AFFECTED_RUNS_ACTIONS = ('requeue', 'cancel')


def _primary_account_identity(provider: str) -> str | None:
    """Primary-slot account identity for this endpoint's provider type.

    Keyed by the type being edited, not the globally selected one: a save
    here moves the account of the type it writes, whichever type is active.
    """
    return account_identity_for_primary_provider(_HOLD_PROVIDER_KEY.get(provider))


def _affected_runs(account_id: str | None, credential_slot: str) -> list[dict]:
    """Active runs whose frozen routes still name this account."""
    if not account_id:
        return []
    from main_app.processing import runs_for_account
    return runs_for_account(account_id, credential_slot)


def _apply_affected_runs_action(runs: list[dict], action: str) -> None:
    """Detach or cancel the runs an account change stranded.

    Both drop the frozen routes so a resumed run re-resolves; 'cancel' also
    asks the owner to stop now rather than at its next provider call.
    """
    from cancel import request_cancellation
    from main_app.processing import clear_route_snapshot
    for run in runs:
        clear_route_snapshot(run['runId'])
        if action == 'cancel':
            request_cancellation(run['slug'], run['episodeId'])


def _affected_runs_payload(runs: list[dict], action: str | None = None) -> dict:
    payload = {'count': len(runs), 'runs': runs}
    if action is not None:
        payload['action'] = action
    return payload


def _source_for(db, cfg) -> str:
    """Report where the *usable* key lives. A DB row that can't be decrypted
    (crypto unavailable, corrupt envelope) counts as absent so GET status
    matches what request-time code will actually resolve."""
    if db.get_setting(cfg['secret']) and (crypto_available() and db.get_secret(cfg['secret'])):
        return 'db'
    if os.environ.get(cfg['env']):
        return 'env'
    return 'none'


def _provider_status(db, cfg):
    source = _source_for(db, cfg)
    entry = {
        'configured': source != 'none',
        'source': source,
    }
    if cfg['base_url']:
        entry['baseUrl'] = db.get_setting(cfg['base_url']) or ''
    if cfg['model']:
        entry['model'] = db.get_setting(cfg['model']) or ''
    return entry


@api.route('/settings/providers', methods=['GET'])
def list_providers():
    db = Database()
    payload = {'cryptoReady': crypto_available()}
    for name, cfg in _PROVIDERS.items():
        payload[name] = _provider_status(db, cfg)
    return json_response(payload, 200)


@api.route('/settings/providers/<provider>', methods=['PUT'])
def update_provider(provider):
    if provider not in _PROVIDERS:
        return error_response('unknown provider', 404)
    if not crypto_available():
        return error_response('provider_crypto_unavailable', 409)

    body = request.get_json(silent=True) or {}
    cfg = _PROVIDERS[provider]
    db = Database()
    credentials_changed = False

    action = body.get('affectedRunsAction', 'requeue')
    if action not in _AFFECTED_RUNS_ACTIONS:
        return error_response(
            f'affectedRunsAction must be one of: {", ".join(_AFFECTED_RUNS_ACTIONS)}', 400)

    if 'apiKey' in body:
        api_key = body['apiKey']
        if api_key is not None and not isinstance(api_key, str):
            return error_response('apiKey must be a string or null', 400)

    if cfg['base_url'] and 'baseUrl' in body:
        url = body['baseUrl']
        if url is not None and not isinstance(url, str):
            return error_response('baseUrl must be a string or null', 400)
        if url:
            if provider in _LLM_PROVIDERS and url_has_userinfo(url):
                return error_response(BASE_URL_USERINFO_ERROR, 400)
            try:
                validate_base_url(url)
            except SSRFError:
                return error_response('base URL failed SSRF validation', 400)

    before_identity = _primary_account_identity(provider)
    previous_calibration = calibration_revision()
    changed_stages = []

    try:
        with db.settings_transaction():
            previous_model_identities = _model_identity_snapshot(db)
            if 'apiKey' in body:
                set_or_clear_secret(db, cfg['secret'], body['apiKey'])
                credentials_changed = True

            if cfg['base_url'] and 'baseUrl' in body:
                url = body['baseUrl']
                if url:
                    db.set_setting(cfg['base_url'], url)
                    credentials_changed = True
                # Empty baseUrl ignored; clear via DELETE /providers/<name>. Issue #235.

            if cfg['model'] and 'model' in body:
                model = body['model'] or ''
                db.set_setting(cfg['model'], model)

            changed_stages = _clear_models_for_identity_changes(
                db, previous_model_identities, {})
    except SecretWriteRejected:
        return error_response('provider_crypto_unavailable', 409)

    # Identity is read either side of the cache flush: before the write the
    # cache is still current, and after it the next read is fresh.
    payload = _finish_provider_write(db, provider, cfg, before_identity, action)

    if changed_stages:
        finish_settings_payload_after_commit(db, changed_stages, previous_calibration)

    if credentials_changed and provider in _LLM_PROVIDERS:
        clear_hold_for_provider_change(
            db, f'{provider} credentials changed',
            provider_key=_HOLD_PROVIDER_KEY.get(provider))

    logger.info("provider=%s updated source=%s", provider, _source_for(db, cfg))
    return json_response(payload, 200)


def _finish_provider_write(db, provider: str, cfg: dict,
                           before_identity: str | None, action: str) -> dict:
    """Flush the provider cache, then report what the write did to the
    primary slot's account identity and to runs frozen against the old one."""
    # Drop the TTL-cached provider settings so the next read sees this write
    # immediately (see issue #234: stale cache made Save Changes vanish).
    from llm_client import invalidate_provider_cache
    invalidate_provider_cache()
    payload = _provider_status(db, cfg)
    if provider not in _LLM_PROVIDERS:
        return payload
    if _primary_account_identity(provider) == before_identity:
        return payload
    runs = _affected_runs(before_identity, SLOT_PRIMARY)
    _apply_affected_runs_action(runs, action)
    payload['accountChanged'] = True
    payload['affectedRuns'] = _affected_runs_payload(runs, action)
    logger.info("provider=%s account identity changed; %d run(s) %sd",
                provider, len(runs), action)
    return payload


@api.route('/settings/providers/<provider>', methods=['DELETE'])
def clear_provider(provider):
    if provider not in _PROVIDERS:
        return error_response('unknown provider', 404)
    cfg = _PROVIDERS[provider]
    db = Database()
    action = (request.get_json(silent=True) or {}).get('affectedRunsAction', 'requeue')
    if action not in _AFFECTED_RUNS_ACTIONS:
        return error_response(
            f'affectedRunsAction must be one of: {", ".join(_AFFECTED_RUNS_ACTIONS)}', 400)
    before_identity = _primary_account_identity(provider)
    previous_calibration = calibration_revision()
    with db.settings_transaction():
        previous_model_identities = _model_identity_snapshot(db)
        db.clear_secret(cfg['secret'])
        if cfg['base_url']:
            db.set_setting(cfg['base_url'], '')
        changed_stages = _clear_models_for_identity_changes(
            db, previous_model_identities, {})
    payload = _finish_provider_write(db, provider, cfg, before_identity, action)
    if changed_stages:
        finish_settings_payload_after_commit(db, changed_stages, previous_calibration)
    if provider in _LLM_PROVIDERS:
        # Lift even with no key left: the next run fails for its own reason.
        clear_hold_for_provider_change(
            db, f'{provider} credentials cleared',
            provider_key=_HOLD_PROVIDER_KEY.get(provider))
    logger.info("provider=%s cleared", provider)
    return json_response(payload, 200)


@api.route('/settings/providers/<slot>/affected-runs', methods=['GET'])
def account_affected_runs(slot):
    """Runs a change to this account slot would strand, for a pre-save check.

    Lists the active runs whose frozen routes still name the slot's current
    account, so the UI can show what a provider or endpoint change is about
    to interrupt before it writes anything.
    """
    slot = SLOT_ALIASES.get(slot, slot)
    if slot not in VALID_SLOTS:
        return error_response('unknown provider slot', 404)
    account_id = account_identity_for_slot(slot)
    return json_response({
        'slot': slot,
        'accountId': account_id,
        'affectedRuns': _affected_runs_payload(_affected_runs(account_id, slot)),
    }, 200)


def _resolve_key(db, cfg):
    if crypto_available():
        val = db.get_secret(cfg['secret'])
        if val:
            return val
    return os.environ.get(cfg['env'])


@api.route('/settings/providers/rotate-passphrase', methods=['POST'])
@limiter.limit('3 per hour')
def rotate_master_passphrase():
    return error_response(
        'Passphrase rotation requires stopped workers; use '
        'scripts/rotate_master_passphrase.py',
        409,
    )


@api.route('/settings/providers/<provider>/test', methods=['POST'])
def test_provider(provider):
    if provider not in _PROVIDERS:
        return error_response('unknown provider', 404)
    cfg = _PROVIDERS[provider]
    db = Database()
    api_key = _resolve_key(db, cfg)
    if not api_key:
        return json_response({'ok': False, 'error': 'no key configured'}, 200)

    if provider in _FIXED_PROVIDER_PROBES:
        url, header_fn = _FIXED_PROVIDER_PROBES[provider]
        headers = header_fn(api_key)
    else:
        base = db.get_setting(cfg['base_url']) or os.environ.get(cfg['base_env'], '')
        if not base:
            return json_response({'ok': False, 'error': 'base URL not configured'}, 200)
        try:
            validate_base_url(base)
        except SSRFError:
            return json_response({'ok': False, 'error': 'base URL failed SSRF validation'}, 200)
        if provider == 'ollama':
            # The real client appends /v1 for Ollama; without it a base URL
            # that works for episodes 404s here.
            base = _normalize_base_url_for_provider(PROVIDER_OLLAMA, base)
        url, headers = _models_request(base, api_key)

    try:
        r = safe_get(
            url,
            trust=URLTrust.OPERATOR_CONFIGURED,
            timeout=HTTP_TIMEOUT_PROBE,
            max_redirects=HTTP_MAX_REDIRECTS_API,
            headers=headers,
        )
    except SSRFError:
        return json_response({'ok': False, 'error': 'base URL failed SSRF validation'}, 200)
    except requests.RequestException:
        logger.exception("provider test failed for %s", provider)
        return json_response({'ok': False, 'error': 'connection failed'}, 200)

    if r.status_code < 400:
        return json_response({'ok': True}, 200)
    return json_response({'ok': False, 'error': f'HTTP {r.status_code}'}, 200)


# Every provider gets a staged connection test: whisper/openai/ollama probe
# the configurable endpoint (accepting unsaved base URLs), fixed-endpoint
# providers probe their public URLs (no baseUrl input).
_CONNECTION_TEST_PROVIDERS = (
    ('whisper', 'openai', 'ollama') + tuple(_FIXED_PROVIDER_PROBES))


def _health_detail(health: dict) -> str:
    """One sentence on a health probe for the connection-test detail, or ''.

    Hedges the count when the probe only established a floor, and reports a
    disagreement rather than one replica's model, so this never contradicts
    the same probe's warning in Settings.
    """
    instances = health.get('instances') or []
    if not health.get('available') or not instances:
        return ''
    count = len(instances)
    noun = 'instance' if count == 1 else 'instances'
    hedge = 'At least ' if health.get('sampled_floor') else ''
    mismatch = health.get('mismatch') or []
    if mismatch:
        fields = ', '.join(f.replace('_', ' ') for f in mismatch)
        return f"{hedge}{count} {noun}, disagreeing on {fields}."
    model = instances[0].get('model')
    return f"{hedge}{count} {noun} reporting {model}." if model else f"{hedge}{count} {noun}."


def _whisper_connection_test(saved: dict, body: dict):
    """Shared whisper-shaped connection probe (#544, #806); resolves against
    `saved`, so the primary and failover whisper routes stay byte-for-byte identical."""
    saved_base, saved_key = saved['api_base_url'], saved['api_key']
    base = body['baseUrl'] if 'baseUrl' in body else saved_base
    if base is not None and not isinstance(base, str):
        return error_response('baseUrl must be a string', 400)
    if not base or not base.strip():
        return json_response(
            {'ok': False, 'reachable': False,
             'detail': 'Enter a base URL first.'}, 200)
    base = base.strip()

    # Saved key goes out only when the tested URL is the saved server (#544).
    api_key = saved_key if _same_server(base, saved_base) else ''

    model = body.get('model') or saved['api_model']
    if not isinstance(model, str):
        return error_response('model must be a string', 400)
    skip_flac = body.get('skipFlacCompression', saved['skip_flac_compression'])
    if not isinstance(skip_flac, bool):
        return error_response('skipFlacCompression must be a boolean', 400)
    result = transcriber.probe_transcription_endpoint(
        base, api_key=api_key, model=model, skip_flac_compression=skip_flac)
    if result.get('ok'):
        # refresh=True: re-probe rather than report a cached result. If a
        # probe for this backend is already running, that one's result is
        # reused instead.
        health = transcriber.probe_whisper_health(
            base_url=base, api_key=api_key, refresh=True)
        result['health'] = health
        summary = _health_detail(health)
        if summary:
            result['detail'] = f"{result['detail']} {summary}"
    return json_response(result, 200)


@api.route('/settings/providers/<provider>/test-connection', methods=['POST'])
def test_provider_connection(provider):
    """End-to-end probe of a configured external endpoint (#544).

    Unlike /test (which needs a stored key and always probes the saved
    settings), this accepts unsaved baseUrl values in the body so the user
    can test before saving; a baseUrl key present in the body is
    authoritative, even when empty, so the test never silently probes a URL
    that is not in the form. whisper uploads a generated audio sample
    through the real transcription request shape; openai/ollama hit the
    same /models route the real LLM client uses for discovery; anthropic
    and openrouter probe their fixed public endpoints (no body input).
    """
    if provider not in _CONNECTION_TEST_PROVIDERS:
        return error_response('unknown provider', 404)

    if provider in _FIXED_PROVIDER_PROBES:
        # Fixed public endpoint: nothing configurable to accept from the
        # body, saved key only.
        api_key = _resolve_key(Database(), _PROVIDERS[provider]) or ''
        return json_response(_probe_fixed_endpoint(provider, api_key), 200)

    body = request.get_json(silent=True) or {}

    if provider == 'whisper':
        # Saved values come from the same resolver the real transcription
        # path uses, so the probe cannot drift from what an episode upload
        # would do. Its base URL is empty when unconfigured, so the key
        # gate inside the helper fails closed.
        return _whisper_connection_test(transcriber._get_whisper_settings(), body)

    # Resolve the default like the real LLM client (DB, then env, then default);
    # the key gate below only ever sees an explicitly saved URL, never that default.
    cfg = _PROVIDERS[provider]
    db = Database()
    saved_base = get_effective_base_url()
    saved_key = _resolve_key(db, cfg) or ''
    gate_base = db.get_setting(cfg['base_url']) \
        or os.environ.get(cfg['base_env'], '')

    base = body['baseUrl'] if 'baseUrl' in body else saved_base
    if base is not None and not isinstance(base, str):
        return error_response('baseUrl must be a string', 400)
    if not base or not base.strip():
        return json_response(
            {'ok': False, 'reachable': False,
             'detail': 'Enter a base URL first.'}, 200)
    base = base.strip()
    if url_has_userinfo(base):
        return error_response(BASE_URL_USERINFO_ERROR, 400)

    # The saved API key goes out only when the tested URL points at the
    # same server as the explicitly saved base URL. Without this gate, any
    # caller with a session could exfiltrate the stored key by "testing" a
    # URL they control -- a secret this API otherwise never returns.
    api_key = saved_key if _same_server(base, gate_base) else ''

    # The real client appends /v1 for Ollama; the probe must match or a
    # URL that works for episodes would fail the test and vice versa.
    norm = _normalize_base_url_for_provider(
        PROVIDER_OLLAMA if provider == 'ollama'
        else PROVIDER_OPENAI_COMPATIBLE, base)
    result = _probe_models_endpoint(norm, api_key)
    return json_response(result, 200)


# Provider types the secondary slot accepts, matching VALID_LLM_PROVIDERS
# in api/settings.py (not importable here without a circular import).
_SECONDARY_PROVIDER_TYPES = (
    PROVIDER_ANTHROPIC, PROVIDER_OPENROUTER, PROVIDER_OPENAI_COMPATIBLE, PROVIDER_OLLAMA)


@api.route('/settings/providers/secondary/test-connection', methods=['POST'])
def test_secondary_provider_connection():
    """End-to-end probe of the optional secondary provider slot.

    Mirrors /settings/providers/<provider>/test-connection above, but reads
    its type, base URL, and key from the secondary_provider_* settings and
    the secondary_provider_api_key secret instead of the primary provider
    config, so the operator can validate the secondary slot before routing
    any stage to it. A fixed-endpoint type (anthropic/openrouter) probes its
    public URL with the secondary key; a configurable-endpoint type
    (openai-compatible, ollama) probes the same /models route the real
    client uses. A `provider` field in the body overrides the saved type
    (like `baseUrl` already does) so an unsaved dropdown change can be
    tested before Save, matching the primary test-connection route. The
    saved key travels only when the requested type still matches the saved
    type and the tested URL is the explicitly saved one.
    """
    db = Database()
    return _llm_slot_connection_test(
        db.get_setting('secondary_provider'), db.get_setting('secondary_provider_base_url') or '',
        get_effective_secondary_provider_api_key, request.get_json(silent=True) or {},
        'Configure a secondary provider type first.')


@api.route('/settings/providers/failover/test-connection', methods=['POST'])
def test_failover_provider_connection():
    """End-to-end probe of the shared LLM failover slot (#806); mirrors
    /settings/providers/secondary/test-connection against failover_llm_* settings."""
    return _llm_slot_connection_test(
        failover.failover_llm_config()['provider'],
        Database().get_setting('failover_llm_base_url') or '',
        get_effective_failover_llm_api_key, request.get_json(silent=True) or {},
        'Configure a failover provider type first.')


def _llm_slot_connection_test(saved_type, gate_base: str, saved_key_fn, body: dict,
                              missing_type_detail: str):
    """Test a provider slot, reusing its saved key only for the saved provider type."""
    provider = body['provider'] if 'provider' in body else saved_type
    if provider is not None and not isinstance(provider, str):
        return error_response('provider must be a string', 400)
    if not provider:
        return json_response(
            {'ok': False, 'reachable': False, 'detail': missing_type_detail}, 200)
    if provider not in _SECONDARY_PROVIDER_TYPES:
        return error_response(
            f'provider must be one of: {", ".join(_SECONDARY_PROVIDER_TYPES)}', 400)

    # The stored key was entered for the saved type, so an unsaved type
    # override must not borrow it: that would ship the key to a vendor the
    # operator never designated.
    saved_key = ''
    if provider == (saved_type or ''):
        saved_key = saved_key_fn() or ''

    if provider in _FIXED_PROVIDER_PROBES:
        return json_response(_probe_fixed_endpoint(provider, saved_key), 200)

    # Effective default matches llm_route; the key gate below sees only an
    # explicitly saved URL, never that default.
    base = body['baseUrl'] if 'baseUrl' in body else (gate_base or DEFAULT_OPENAI_BASE_URL)
    if base is not None and not isinstance(base, str):
        return error_response('baseUrl must be a string', 400)
    if not base or not base.strip():
        return json_response(
            {'ok': False, 'reachable': False,
             'detail': 'Enter a base URL first.'}, 200)
    base = base.strip()
    if url_has_userinfo(base):
        return error_response(BASE_URL_USERINFO_ERROR, 400)

    # Same anti-exfiltration gate as the primary test-connection route
    # (#544): the saved key only goes out when the tested URL matches the
    # explicitly saved base URL.
    api_key = saved_key if _same_server(base, gate_base) else ''

    norm = _normalize_base_url_for_provider(
        PROVIDER_OLLAMA if provider == PROVIDER_OLLAMA
        else PROVIDER_OPENAI_COMPATIBLE, base)
    return json_response(_probe_models_endpoint(norm, api_key), 200)


@api.route('/settings/providers/failover-whisper/test-connection', methods=['POST'])
def test_failover_whisper_connection():
    """End-to-end probe of the whisper failover slot (#806); same contract as the
    primary whisper route, reading saved values via _get_failover_whisper_settings."""
    body = request.get_json(silent=True) or {}
    return _whisper_connection_test(transcriber._get_failover_whisper_settings(), body)
