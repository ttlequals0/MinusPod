"""Provider API key management: /settings/providers/*

Stores LLM/Whisper credentials encrypted at rest. GET never returns key
values (booleans + source only). All outbound base URLs pass SSRF validation.
"""
import logging
import os

import requests
from flask import request

from api import api, error_response, json_response
from database import Database
from secrets_crypto import CryptoUnavailableError, is_available as crypto_available, rotate as rotate_passphrase
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
        if api_key is None or api_key == '':
            db.clear_secret(cfg['secret'])
        elif isinstance(api_key, str):
            db.set_secret(cfg['secret'], api_key.strip())
        else:
            return error_response('apiKey must be a string or null', 400)

    if cfg['base_url'] and 'baseUrl' in body:
        url = body['baseUrl']
        if url:
            try:
                validate_base_url(url)
            except SSRFError:
                return error_response('base URL failed SSRF validation', 400)
            db.set_setting(cfg['base_url'], url)
        else:
            db.set_setting(cfg['base_url'], '')

    if cfg['model'] and 'model' in body:
        model = body['model'] or ''
        db.set_setting(cfg['model'], model)

    logger.info("provider=%s updated source=%s", provider, _source_for(db, cfg))
    return json_response(_provider_status(db, cfg), 200)


@api.route('/settings/providers/<provider>', methods=['DELETE'])
def clear_provider(provider):
    if provider not in _PROVIDERS:
        return error_response('unknown provider', 404)
    cfg = _PROVIDERS[provider]
    db = Database()
    db.clear_secret(cfg['secret'])
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
        # secrets_crypto.rotate raises ValueError only with static, non-sensitive
        # messages ("current passphrase mismatch", "new passphrase required",
        # "must differ from current"). Do not relax this contract.
        return error_response(str(e), 400)
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
        url = base.rstrip('/') + '/models'
        headers = {'Authorization': f'Bearer {api_key}'}

    try:
        r = requests.get(url, headers=headers, timeout=5)
    except requests.RequestException:
        logger.exception("provider test failed for %s", provider)
        return json_response({'ok': False, 'error': 'connection failed'}, 200)

    if r.status_code < 400:
        return json_response({'ok': True}, 200)
    return json_response({'ok': False, 'error': f'HTTP {r.status_code}'}, 200)
