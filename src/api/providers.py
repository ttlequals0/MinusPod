"""Provider API key management: /settings/providers/*

Stores LLM/Whisper credentials encrypted at rest. GET never returns key
values (booleans + source only). All outbound base URLs pass SSRF validation.
"""
import logging
import os
from urllib.parse import urlparse

import requests
from flask import request

import transcriber
from api import api, error_response, json_response
from config import (
    HTTP_MAX_REDIRECTS_API, HTTP_TIMEOUT_PROBE,
    PROVIDER_OLLAMA, PROVIDER_OPENAI_COMPATIBLE,
)
from database import Database
from llm_client import get_effective_base_url, _normalize_base_url_for_provider
from secrets_crypto import CryptoUnavailableError, is_available as crypto_available, rotate as rotate_passphrase
from utils.connection_probe import run_probe, parse_probe_json, rejected_detail
from utils.http import safe_url_for_log
from utils.safe_http import URLTrust, safe_get
from utils.secret_writes import SecretWriteRejected, set_or_clear_secret
from utils.url import validate_base_url, SSRFError

logger = logging.getLogger(__name__)

_PROVIDERS = {
    'anthropic':  {'secret': 'anthropic_api_key',  'base_url': None,                  'base_env': None,                 'model': None,                'env': 'ANTHROPIC_API_KEY'},
    'openai':     {'secret': 'openai_api_key',     'base_url': 'openai_base_url',     'base_env': 'OPENAI_BASE_URL',    'model': None,                'env': 'OPENAI_API_KEY'},
    'openrouter': {'secret': 'openrouter_api_key', 'base_url': None,                  'base_env': None,                 'model': None,                'env': 'OPENROUTER_API_KEY'},
    'whisper':    {'secret': 'whisper_api_key',    'base_url': 'whisper_api_base_url','base_env': 'WHISPER_API_BASE_URL','model': 'whisper_api_model', 'env': 'WHISPER_API_KEY'},
    'ollama':     {'secret': 'ollama_api_key',     'base_url': 'openai_base_url',     'base_env': 'OPENAI_BASE_URL',    'model': None,                'env': 'OLLAMA_API_KEY'},
}


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

    if 'apiKey' in body:
        api_key = body['apiKey']
        if api_key is not None and not isinstance(api_key, str):
            return error_response('apiKey must be a string or null', 400)
        try:
            set_or_clear_secret(db, cfg['secret'], api_key)
        except SecretWriteRejected:
            return error_response('provider_crypto_unavailable', 409)

    if cfg['base_url'] and 'baseUrl' in body:
        url = body['baseUrl']
        if url:
            try:
                validate_base_url(url)
            except SSRFError:
                return error_response('base URL failed SSRF validation', 400)
            db.set_setting(cfg['base_url'], url)
        # Empty baseUrl ignored; clear via DELETE /providers/<name>. Issue #235.

    if cfg['model'] and 'model' in body:
        model = body['model'] or ''
        db.set_setting(cfg['model'], model)

    # Drop the TTL-cached provider settings so the next read sees this write
    # immediately (see issue #234: stale cache made Save Changes vanish).
    from llm_client import invalidate_provider_cache
    invalidate_provider_cache()

    logger.info("provider=%s updated source=%s", provider, _source_for(db, cfg))
    return json_response(_provider_status(db, cfg), 200)


@api.route('/settings/providers/<provider>', methods=['DELETE'])
def clear_provider(provider):
    if provider not in _PROVIDERS:
        return error_response('unknown provider', 404)
    cfg = _PROVIDERS[provider]
    db = Database()
    db.clear_secret(cfg['secret'])
    if cfg['base_url']:
        db.set_setting(cfg['base_url'], '')
    from llm_client import invalidate_provider_cache
    invalidate_provider_cache()
    logger.info("provider=%s cleared", provider)
    return json_response(_provider_status(db, cfg), 200)


def _resolve_key(db, cfg):
    if crypto_available():
        val = db.get_secret(cfg['secret'])
        if val:
            return val
    return os.environ.get(cfg['env'])


@api.route('/settings/providers/rotate-passphrase', methods=['POST'])
def rotate_master_passphrase():
    if not crypto_available():
        return error_response('provider_crypto_unavailable', 409)
    body = request.get_json(silent=True) or {}
    old = body.get('oldPassphrase')
    new = body.get('newPassphrase')
    if not isinstance(old, str) or not isinstance(new, str) or not old or not new:
        return error_response('oldPassphrase and newPassphrase required', 400)
    db = Database()
    try:
        rotated = rotate_passphrase(db, old, new)
    except CryptoUnavailableError:
        return error_response('provider_crypto_unavailable', 409)
    except ValueError as e:
        # Only pass through the known static error strings documented by
        # secrets_crypto.rotate; anything else is logged server-side and
        # surfaced as a generic 400 so exception messages cannot leak.
        safe_rotation_errors = {
            "current passphrase mismatch",
            "new passphrase required",
            "must differ from current",
        }
        msg = str(e)
        if msg in safe_rotation_errors:
            return error_response(msg, 400)
        logger.warning("Unexpected ValueError from rotate_passphrase: %s", e)
        return error_response('invalid rotation request', 400)
    except Exception:
        logger.exception("provider passphrase rotation failed")
        return error_response('rotation failed', 500)
    return json_response({'rotated': rotated}, 200)


@api.route('/settings/providers/<provider>/test', methods=['POST'])
def test_provider(provider):
    if provider not in _PROVIDERS:
        return error_response('unknown provider', 404)
    cfg = _PROVIDERS[provider]
    db = Database()
    api_key = _resolve_key(db, cfg)
    if not api_key:
        return json_response({'ok': False, 'error': 'no key configured'}, 200)

    if provider == 'anthropic':
        url = 'https://api.anthropic.com/v1/models'
        headers = {'x-api-key': api_key, 'anthropic-version': '2023-06-01'}
    elif provider == 'openrouter':
        url = 'https://openrouter.ai/api/v1/auth/key'
        headers = {'Authorization': f'Bearer {api_key}'}
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


def _same_server(url_a: str, url_b: str) -> bool:
    """True when two base URLs point at the same scheme/host/port."""
    if not url_a or not url_b:
        return False
    try:
        a, b = urlparse(url_a), urlparse(url_b)
        return (a.scheme, a.hostname, a.port) == (b.scheme, b.hostname, b.port)
    except ValueError:
        # Malformed port in a hand-typed URL; never a match.
        return False


def _models_request(base_url: str, api_key: str):
    """URL + auth headers for an OpenAI-compatible /models request. Shared
    by /test and /test-connection so the discovery contract lives once."""
    url = base_url.rstrip('/') + '/models'
    headers = {'Authorization': f'Bearer {api_key}'} if api_key else {}
    return url, headers


def _probe_models_endpoint(base_url: str, api_key: str) -> dict:
    """Staged connection probe for an OpenAI-compatible LLM endpoint.

    GET {base}/models -- the same discovery route the real client uses on
    startup -- with the same optional bearer auth. Unlike /test it needs no
    stored key (local Ollama has none) and reports which failure class the
    caller is in rather than a bare pass/fail.
    """
    url, headers = _models_request(base_url, api_key)
    error, status, body_bytes = run_probe(
        lambda: safe_get(
            url,
            trust=URLTrust.OPERATOR_CONFIGURED,
            timeout=HTTP_TIMEOUT_PROBE,
            max_redirects=HTTP_MAX_REDIRECTS_API,
            headers=headers,
            stream=True,
        ),
        HTTP_TIMEOUT_PROBE,
        log_context=safe_url_for_log(url),
    )
    if error:
        return error

    result = {'ok': False, 'reachable': True, 'status': status}
    if status < 400:
        body = parse_probe_json(body_bytes)
        # The real client reads response.data as the model array
        # (llm_client list_models); a green result must mean discovery
        # will actually work, not just that some JSON came back.
        if isinstance(body, dict) and isinstance(body.get('data'), list):
            result['ok'] = True
            result['detail'] = (f'Connected. The server returned its model '
                                f'list (HTTP {status}).')
        else:
            result['detail'] = (f'The server answered HTTP {status} but did '
                                'not return a model list. Check that the URL '
                                'points at an OpenAI-compatible API.')
    elif status in (401, 403):
        if api_key:
            result['detail'] = (f'The server rejected the saved API key '
                                f'(HTTP {status}). Check the key.')
        else:
            result['detail'] = (f'The endpoint requires an API key '
                                f'(HTTP {status}). The test sends the saved '
                                'key, and only when the tested URL matches '
                                'the saved one -- save your key and base '
                                'URL, then test again.')
    elif status == 404:
        result['detail'] = ('The server is running, but there is no models '
                            'endpoint at this path (HTTP 404). The base URL '
                            'usually ends in /v1.')
    else:
        result['detail'] = rejected_detail(status, body_bytes)
    return result


# Providers with a configurable endpoint worth probing. Anthropic and
# OpenRouter talk to fixed public URLs; their /test key check is the whole
# story, so they are deliberately absent here.
_CONNECTION_TEST_PROVIDERS = ('whisper', 'openai', 'ollama')


@api.route('/settings/providers/<provider>/test-connection', methods=['POST'])
def test_provider_connection(provider):
    """End-to-end probe of a configured external endpoint (#544).

    Unlike /test (which needs a stored key and always probes the saved
    settings), this accepts unsaved baseUrl values in the body so the user
    can test before saving; a baseUrl key present in the body is
    authoritative, even when empty, so the test never silently probes a URL
    that is not in the form. whisper uploads a generated audio sample
    through the real transcription request shape; openai/ollama hit the
    same /models route the real LLM client uses for discovery.
    """
    if provider not in _CONNECTION_TEST_PROVIDERS:
        return error_response('unknown provider', 404)
    body = request.get_json(silent=True) or {}

    cfg = _PROVIDERS[provider]
    if provider == 'whisper':
        # Saved values come from the same resolver the real transcription
        # path uses, so the probe cannot drift from what an episode upload
        # would do. Its base URL is empty when unconfigured, so the key
        # gate below fails closed.
        saved = transcriber._get_whisper_settings()
        saved_base, saved_key = saved['api_base_url'], saved['api_key']
        gate_base = saved_base
    else:
        # For the default probe target, use the same resolution the real
        # LLM client does (DB, then env, then the documented default). The
        # key gate must NOT see that default: only a URL the operator
        # explicitly saved may receive the key, otherwise "testing" the
        # never-configured default URL would ship the key to whatever
        # listens there.
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

    # The saved API key goes out only when the tested URL points at the
    # same server as the explicitly saved base URL. Without this gate, any
    # caller with a session could exfiltrate the stored key by "testing" a
    # URL they control -- a secret this API otherwise never returns.
    api_key = saved_key if _same_server(base, gate_base) else ''

    if provider == 'whisper':
        model = body.get('model') or saved['api_model']
        if not isinstance(model, str):
            return error_response('model must be a string', 400)
        skip_flac = body.get('skipFlacCompression',
                             saved['skip_flac_compression'])
        if not isinstance(skip_flac, bool):
            return error_response('skipFlacCompression must be a boolean', 400)
        result = transcriber.probe_transcription_endpoint(
            base, api_key=api_key, model=model,
            skip_flac_compression=skip_flac)
    else:
        # The real client appends /v1 for Ollama; the probe must match or a
        # URL that works for episodes would fail the test and vice versa.
        norm = _normalize_base_url_for_provider(
            PROVIDER_OLLAMA if provider == 'ollama'
            else PROVIDER_OPENAI_COMPATIBLE, base)
        result = _probe_models_endpoint(norm, api_key)
    return json_response(result, 200)
