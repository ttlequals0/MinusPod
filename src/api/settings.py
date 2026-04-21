"""Settings routes: /settings/* endpoints."""
import json
import logging
import os
import re
import threading
import uuid

from flask import request

from api import (
    api, log_request, json_response, error_response,
    get_database, _enrich_models_with_pricing, limiter,
)
from config import (
    WHISPER_BACKEND_LOCAL, WHISPER_BACKEND_API,
    WHISPER_COMPUTE_TYPES, WHISPER_COMPUTE_TYPE_DEFAULT,
    OPENROUTER_BASE_URL,
    PROVIDER_ANTHROPIC, PROVIDER_OPENROUTER, PROVIDER_OPENAI_COMPATIBLE, PROVIDER_OLLAMA,
)
from pricing_fetcher import force_refresh_pricing
from llm_client import (
    get_effective_provider, get_effective_base_url, get_api_key, get_effective_openrouter_api_key,
    get_llm_client, create_client_for_provider, _JSON_FORMAT_SETTING_KEY,
)
from utils.url import validate_url, validate_base_url, SSRFError
from utils.http import safe_url_for_log
from utils.secret_writes import SecretWriteRejected, set_or_clear_secret
from webhook_service import render_template_preview, fire_test_event, load_webhooks, VALID_EVENTS

logger = logging.getLogger('podcast.api')


def _setting_value(settings, key, default=None):
    """Extract value from the settings dict returned by get_all_settings()."""
    return settings.get(key, {}).get('value', default)


def _setting_is_default(settings, key):
    """Check if a setting is still at its default value."""
    return settings.get(key, {}).get('is_default', True)


# ========== Settings Endpoints ==========

@api.route('/settings', methods=['GET'])
@log_request
def get_settings():
    """Get all settings."""
    db = get_database()
    from database import DEFAULT_SYSTEM_PROMPT, DEFAULT_VERIFICATION_PROMPT
    from ad_detector import AdDetector
    from config import DEFAULT_AD_DETECTION_MODEL as DEFAULT_MODEL
    from chapters_generator import CHAPTERS_MODEL
    settings = db.get_all_settings()

    # Shorthand for building {value, isDefault} response dicts
    def _sv(key, value=None):
        """Build a setting value response dict."""
        return {
            'value': value if value is not None else _setting_value(settings, key),
            'isDefault': _setting_is_default(settings, key),
        }

    # Get current model settings
    current_model = _setting_value(settings, 'claude_model', DEFAULT_MODEL)
    verification_model = _setting_value(settings, 'verification_model', DEFAULT_MODEL)
    chapters_model = _setting_value(settings, 'chapters_model', CHAPTERS_MODEL)

    # Get whisper model setting (defaults to env var or 'small')
    default_whisper_model = os.environ.get('WHISPER_MODEL', 'small')
    whisper_model = _setting_value(settings, 'whisper_model', default_whisper_model)

    # Get boolean settings
    auto_process_value = _setting_value(settings, 'auto_process_enabled', 'true')
    auto_process_enabled = auto_process_value.lower() in ('true', '1', 'yes')
    vtt_value = _setting_value(settings, 'vtt_transcripts_enabled', 'true')
    vtt_enabled = vtt_value.lower() in ('true', '1', 'yes')
    chapters_value = _setting_value(settings, 'chapters_enabled', 'true')
    chapters_enabled = chapters_value.lower() in ('true', '1', 'yes')

    # Get min cut confidence (ad detection aggressiveness)
    try:
        min_cut_confidence = float(_setting_value(settings, 'min_cut_confidence', '0.80'))
    except (ValueError, TypeError):
        min_cut_confidence = 0.80

    # LLM provider settings
    llm_provider = get_effective_provider()
    openai_base_url = get_effective_base_url()
    api_key = get_api_key()
    api_key_configured = bool(api_key and api_key != 'not-needed')
    openrouter_api_key = get_effective_openrouter_api_key()
    openrouter_api_key_configured = bool(openrouter_api_key)

    podcast_index_api_key = _setting_value(settings, 'podcast_index_api_key', '') or os.environ.get('PODCAST_INDEX_API_KEY', '')

    # Whisper backend settings (env var defaults)
    default_whisper_backend = os.environ.get('WHISPER_BACKEND', 'local')
    default_whisper_api_base_url = os.environ.get('WHISPER_API_BASE_URL', '')
    default_whisper_api_model = os.environ.get('WHISPER_API_MODEL', 'whisper-1')
    default_whisper_language = os.environ.get('WHISPER_LANGUAGE') or 'en'
    default_whisper_compute_type = os.environ.get('WHISPER_COMPUTE_TYPE', WHISPER_COMPUTE_TYPE_DEFAULT)
    whisper_backend = _setting_value(settings, 'whisper_backend', default_whisper_backend)
    whisper_api_base_url = _setting_value(settings, 'whisper_api_base_url', default_whisper_api_base_url)
    whisper_api_key = _setting_value(settings, 'whisper_api_key', '')
    whisper_api_model = _setting_value(settings, 'whisper_api_model', default_whisper_api_model)
    whisper_language = _setting_value(settings, 'whisper_language', default_whisper_language)
    whisper_compute_type = _setting_value(settings, 'whisper_compute_type', default_whisper_compute_type)

    return json_response({
        'systemPrompt': _sv('system_prompt', _setting_value(settings, 'system_prompt', DEFAULT_SYSTEM_PROMPT)),
        'verificationPrompt': _sv('verification_prompt', _setting_value(settings, 'verification_prompt', DEFAULT_VERIFICATION_PROMPT)),
        'claudeModel': _sv('claude_model', current_model),
        'verificationModel': _sv('verification_model', verification_model),
        'whisperModel': _sv('whisper_model', whisper_model),
        'autoProcessEnabled': _sv('auto_process_enabled', auto_process_enabled),
        'vttTranscriptsEnabled': _sv('vtt_transcripts_enabled', vtt_enabled),
        'chaptersEnabled': _sv('chapters_enabled', chapters_enabled),
        'chaptersModel': _sv('chapters_model', chapters_model),
        'minCutConfidence': _sv('min_cut_confidence', min_cut_confidence),
        'llmProvider': _sv('llm_provider', llm_provider),
        'openaiBaseUrl': _sv('openai_base_url', openai_base_url),
        'openrouterApiKeyConfigured': openrouter_api_key_configured,
        'podcastIndexApiKeyConfigured': bool(podcast_index_api_key),
        'openrouterBaseUrl': OPENROUTER_BASE_URL,
        'whisperBackend': _sv('whisper_backend', whisper_backend),
        'whisperApiBaseUrl': _sv('whisper_api_base_url', whisper_api_base_url),
        'whisperApiKeyConfigured': bool(whisper_api_key),
        'whisperApiModel': _sv('whisper_api_model', whisper_api_model),
        'whisperLanguage': _sv('whisper_language', whisper_language),
        'whisperComputeType': _sv('whisper_compute_type', whisper_compute_type),
        'apiKeyConfigured': api_key_configured,
        'retentionDays': int(db.get_setting('retention_days') or '30'),
        'defaults': {
            'systemPrompt': DEFAULT_SYSTEM_PROMPT,
            'verificationPrompt': DEFAULT_VERIFICATION_PROMPT,
            'claudeModel': DEFAULT_MODEL,
            'verificationModel': DEFAULT_MODEL,
            'whisperModel': default_whisper_model,
            'autoProcessEnabled': True,
            'vttTranscriptsEnabled': True,
            'chaptersEnabled': True,
            'chaptersModel': CHAPTERS_MODEL,
            'minCutConfidence': 0.80,
            'llmProvider': os.environ.get('LLM_PROVIDER', PROVIDER_ANTHROPIC),
            'openaiBaseUrl': os.environ.get('OPENAI_BASE_URL', 'http://localhost:8000/v1'),
            'openrouterBaseUrl': OPENROUTER_BASE_URL,
            'whisperBackend': default_whisper_backend,
            'whisperApiBaseUrl': default_whisper_api_base_url,
            'whisperApiModel': default_whisper_api_model,
            'whisperLanguage': default_whisper_language,
            'whisperComputeType': default_whisper_compute_type,
        }
    })


@api.route('/settings/ad-detection', methods=['PUT'])
@log_request
def update_ad_detection_settings():
    """Update ad detection settings."""
    data = request.get_json()

    if not data:
        return error_response('Request body required', 400)

    db = get_database()

    if 'systemPrompt' in data:
        db.set_setting('system_prompt', data['systemPrompt'], is_default=False)
        logger.info("Updated system prompt")

    if 'verificationPrompt' in data:
        db.set_setting('verification_prompt', data['verificationPrompt'], is_default=False)
        logger.info("Updated verification prompt")

    if 'claudeModel' in data:
        db.set_setting('claude_model', data['claudeModel'], is_default=False)
        logger.info(f"Updated Claude model to: {data['claudeModel']}")

    if 'verificationModel' in data:
        db.set_setting('verification_model', data['verificationModel'], is_default=False)
        logger.info(f"Updated verification model to: {data['verificationModel']}")

    if 'whisperModel' in data:
        db.set_setting('whisper_model', data['whisperModel'], is_default=False)
        logger.info(f"Updated Whisper model to: {data['whisperModel']}")
        # Trigger model reload on next transcription
        try:
            from transcriber import WhisperModelSingleton
            WhisperModelSingleton.mark_for_reload()
        except Exception as e:
            logger.warning(f"Could not mark model for reload: {e}")

    if 'autoProcessEnabled' in data:
        value = 'true' if data['autoProcessEnabled'] else 'false'
        db.set_setting('auto_process_enabled', value, is_default=False)
        logger.info(f"Updated auto-process to: {value}")

    if 'vttTranscriptsEnabled' in data:
        value = 'true' if data['vttTranscriptsEnabled'] else 'false'
        db.set_setting('vtt_transcripts_enabled', value, is_default=False)
        logger.info(f"Updated VTT transcripts to: {value}")

    if 'chaptersEnabled' in data:
        value = 'true' if data['chaptersEnabled'] else 'false'
        db.set_setting('chapters_enabled', value, is_default=False)
        logger.info(f"Updated chapters generation to: {value}")

    if 'chaptersModel' in data:
        db.set_setting('chapters_model', data['chaptersModel'], is_default=False)
        logger.info(f"Updated chapters model to: {data['chaptersModel']}")

    if 'minCutConfidence' in data:
        # Clamp to valid range (0.50 - 0.95)
        value = max(0.50, min(0.95, float(data['minCutConfidence'])))
        db.set_setting('min_cut_confidence', str(value), is_default=False)
        logger.info(f"Updated min cut confidence to: {value}")

    provider_changed = False
    if 'llmProvider' in data:
        valid_llm_providers = (
            PROVIDER_ANTHROPIC, PROVIDER_OPENROUTER,
            PROVIDER_OPENAI_COMPATIBLE, PROVIDER_OLLAMA,
        )
        if data['llmProvider'] not in valid_llm_providers:
            return json_response(
                {'error': f'llmProvider must be one of: {", ".join(valid_llm_providers)}'}, 400
            )
        db.set_setting('llm_provider', data['llmProvider'], is_default=False)
        logger.info(f"Updated LLM provider to: {data['llmProvider']}")
        provider_changed = True

    if 'openaiBaseUrl' in data:
        try:
            validate_base_url(data['openaiBaseUrl'])
        except SSRFError as e:
            return json_response({'error': f'Invalid base URL: {e}'}, 400)
        db.set_setting('openai_base_url', data['openaiBaseUrl'], is_default=False)
        logger.info(f"Updated OpenAI base URL to: {data['openaiBaseUrl']}")
        provider_changed = True

    if 'openrouterApiKey' in data:
        key = data['openrouterApiKey'].strip()
        if key and not key.startswith('sk-or-'):
            return json_response({'error': 'OpenRouter API key must start with sk-or-'}, 400)
        try:
            set_or_clear_secret(db, 'openrouter_api_key', key)
        except SecretWriteRejected:
            return error_response('provider_crypto_unavailable', 409)
        logger.info("Updated OpenRouter API key")
        provider_changed = True

    if provider_changed:
        # Clear cached json_format probe so the new endpoint gets re-probed
        db.set_setting(_JSON_FORMAT_SETTING_KEY, '', is_default=True)
        client = get_llm_client(force_new=True)
        if hasattr(client, 'probe_json_format_support'):
            client.probe_json_format_support()
        threading.Thread(target=force_refresh_pricing, daemon=True).start()

        # Prune saved model IDs that the new provider does not advertise.
        # Otherwise selections from a prior catalog (e.g. OpenRouter-style
        # tags carrying into Ollama Cloud) survive the switch and fail at
        # request time with not_found_error. Reset to provider-aware defaults.
        try:
            advertised = {m.id for m in client.list_models()}
            for setting_key in ('claude_model', 'verification_model', 'chapters_model'):
                current = db.get_setting(setting_key)
                if current and current not in advertised:
                    logger.info(
                        "Resetting %s='%s' on provider change: not advertised by new provider",
                        setting_key, current,
                    )
                    db.reset_setting(setting_key)
        except Exception:
            logger.exception("Failed to prune stale model selections after provider change")

    if 'whisperBackend' in data:
        valid_whisper_backends = (WHISPER_BACKEND_LOCAL, WHISPER_BACKEND_API)
        if data['whisperBackend'] not in valid_whisper_backends:
            return json_response(
                {'error': f'whisperBackend must be one of: {", ".join(valid_whisper_backends)}'}, 400
            )
        db.set_setting('whisper_backend', data['whisperBackend'], is_default=False)
        logger.info(f"Updated whisper backend to: {data['whisperBackend']}")

    if 'whisperApiBaseUrl' in data:
        if data['whisperApiBaseUrl']:
            try:
                validate_base_url(data['whisperApiBaseUrl'])
            except SSRFError as e:
                return json_response({'error': f'Invalid whisper API base URL: {e}'}, 400)
        db.set_setting('whisper_api_base_url', data['whisperApiBaseUrl'], is_default=False)
        logger.info(f"Updated whisper API base URL to: {data['whisperApiBaseUrl']}")

    if 'whisperApiKey' in data:
        try:
            set_or_clear_secret(db, 'whisper_api_key', data['whisperApiKey'])
        except SecretWriteRejected:
            return error_response('provider_crypto_unavailable', 409)
        logger.info("Updated whisper API key")

    if 'whisperApiModel' in data:
        model_val = str(data['whisperApiModel']).strip()
        if not model_val or len(model_val) > 200:
            return json_response({'error': 'whisperApiModel must be a non-empty string (max 200 chars)'}, 400)
        db.set_setting('whisper_api_model', model_val, is_default=False)
        logger.info(f"Updated whisper API model to: {model_val}")

    if 'whisperLanguage' in data:
        lang_val = str(data['whisperLanguage']).strip().lower()
        # Empty string collapses to default ('en'); 'auto' is allowed; otherwise
        # require a plausible ISO-639-1 code shape (2-5 alphanum/hyphen chars).
        if lang_val and lang_val != 'auto' and not re.match(r'^[a-z]{2,3}(-[a-z0-9]{2,4})?$', lang_val):
            return json_response({'error': "whisperLanguage must be 'auto' or a valid language code (e.g. 'en', 'fi', 'pt-br')"}, 400)
        db.set_setting('whisper_language', lang_val or 'en', is_default=False)
        logger.info(f"Updated whisper language to: {lang_val or 'en'}")

    if 'whisperComputeType' in data:
        ct_val = str(data['whisperComputeType']).strip()
        if ct_val not in WHISPER_COMPUTE_TYPES:
            return json_response(
                {'error': f'whisperComputeType must be one of: {", ".join(WHISPER_COMPUTE_TYPES)}'}, 400
            )
        db.set_setting('whisper_compute_type', ct_val, is_default=False)
        # Trigger model reload on next transcription so the new compute type takes effect.
        try:
            from transcriber import WhisperModelSingleton
            WhisperModelSingleton.mark_for_reload()
        except Exception:
            logger.exception("Failed to mark Whisper model for reload after compute_type change")
        logger.info(f"Updated whisper compute type to: {ct_val}")

    if 'podcastIndexApiKey' in data:
        try:
            set_or_clear_secret(db, 'podcast_index_api_key', data['podcastIndexApiKey'])
        except SecretWriteRejected:
            return error_response('provider_crypto_unavailable', 409)
        logger.info("Updated Podcast Index API key")

    if 'podcastIndexApiSecret' in data:
        try:
            set_or_clear_secret(db, 'podcast_index_api_secret', data['podcastIndexApiSecret'])
        except SecretWriteRejected:
            return error_response('provider_crypto_unavailable', 409)
        logger.info("Updated Podcast Index API secret")

    return json_response({'message': 'Settings updated'})


@api.route('/settings/ad-detection/reset', methods=['POST'])
@log_request
def reset_ad_detection_settings():
    """Reset ad detection settings to defaults."""
    db = get_database()

    db.reset_setting('system_prompt')
    db.reset_setting('verification_prompt')
    db.reset_setting('claude_model')
    db.reset_setting('verification_model')
    db.reset_setting('whisper_model')
    db.reset_setting('vtt_transcripts_enabled')
    db.reset_setting('chapters_enabled')
    db.reset_setting('chapters_model')

    db.reset_setting('min_cut_confidence')
    db.reset_setting('auto_process_enabled')

    # Reset LLM provider settings back to env var defaults
    db.reset_setting('llm_provider')
    db.reset_setting('openai_base_url')
    db.reset_setting('openrouter_api_key')
    db.set_setting(_JSON_FORMAT_SETTING_KEY, '', is_default=True)

    # Reset whisper backend settings
    db.reset_setting('whisper_backend')
    db.reset_setting('whisper_api_base_url')
    db.reset_setting('whisper_api_key')
    db.reset_setting('whisper_api_model')
    db.reset_setting('whisper_compute_type')

    # Recreate LLM client with reset settings
    client = get_llm_client(force_new=True)
    if hasattr(client, 'probe_json_format_support'):
        client.probe_json_format_support()

    # Mark whisper model for reload
    try:
        from transcriber import WhisperModelSingleton
        WhisperModelSingleton.mark_for_reload()
    except Exception as e:
        logger.warning(f"Could not mark model for reload: {e}")

    logger.info("Reset all settings to defaults")
    return json_response({'message': 'Settings reset to defaults'})


@api.route('/settings/prompts/reset', methods=['POST'])
@log_request
def reset_prompts_only():
    """Reset only the prompts to defaults (not models or other settings)."""
    db = get_database()

    db.reset_setting('system_prompt')
    db.reset_setting('verification_prompt')

    logger.info("Reset prompts to defaults")
    return json_response({'message': 'Prompts reset to defaults'})


@api.route('/settings/models', methods=['GET'])
@log_request
def get_available_models():
    """Get list of available models for the current or requested provider.

    Accepts optional ?provider= query param to preview models for a different
    provider before saving settings.
    """
    provider_override = request.args.get('provider')

    if provider_override:
        valid_providers = (
            PROVIDER_ANTHROPIC, PROVIDER_OPENROUTER,
            PROVIDER_OPENAI_COMPATIBLE, PROVIDER_OLLAMA,
        )
        if provider_override not in valid_providers:
            return error_response(
                f'provider must be one of: {", ".join(valid_providers)}', 400
            )
        client = create_client_for_provider(provider_override)
        if client:
            try:
                raw_models = client.list_models()
                models = [
                    {'id': m.id, 'name': m.name, 'created': m.created}
                    for m in raw_models
                ]
            except ValueError as e:
                # Expected when a provider has no key configured yet (e.g. UI
                # previewing providers before the user saves a key).
                logger.info(f"Provider '{provider_override}' preview unavailable: {e}")
                models = []
            except Exception as e:
                logger.error(f"Failed to list models for provider '{provider_override}': {e}")
                models = []
        else:
            models = []
    else:
        from ad_detector import AdDetector
        ad_detector = AdDetector()
        models = ad_detector.get_available_models()

    _enrich_models_with_pricing(models)
    return json_response({'models': models})


@api.route('/settings/models/refresh', methods=['POST'])
@log_request
def refresh_models():
    """Force refresh the model list from the LLM provider.

    ``get_llm_client(force_new=True)`` rebuilds the client and clears
    ``_model_list_cache`` in llm_client, so the next ``list_models()``
    call repopulates from upstream.
    """
    from ad_detector import AdDetector

    get_llm_client(force_new=True)
    ad_detector = AdDetector()
    models = ad_detector.get_available_models()
    _enrich_models_with_pricing(models)

    logger.info(f"Refreshed model list: {len(models)} models available")
    return json_response({'models': models, 'count': len(models)})


@api.route('/settings/whisper-models', methods=['GET'])
@log_request
def get_whisper_models():
    """Get list of available Whisper models with resource requirements."""
    models = [
        {
            'id': 'tiny',
            'name': 'Tiny',
            'vram': '~1GB',
            'speed': '~1 min/60min',
            'quality': 'Basic'
        },
        {
            'id': 'base',
            'name': 'Base',
            'vram': '~1GB',
            'speed': '~1.5 min/60min',
            'quality': 'Good'
        },
        {
            'id': 'small',
            'name': 'Small (Default)',
            'vram': '~2GB',
            'speed': '~2-3 min/60min',
            'quality': 'Better'
        },
        {
            'id': 'medium',
            'name': 'Medium',
            'vram': '~4GB',
            'speed': '~4-5 min/60min',
            'quality': '~15% better than Small'
        },
        {
            'id': 'large-v3',
            'name': 'Large v3',
            'vram': '~5-6GB',
            'speed': '~6-8 min/60min',
            'quality': '~25% better than Small'
        }
    ]
    return json_response({'models': models})


@api.route('/networks', methods=['GET'])
@log_request
def list_networks():
    """List all known podcast networks for network override selection."""
    from pattern_service import KNOWN_NETWORKS

    networks = [
        {'id': network_id, 'name': network_id.replace('_', ' ').title()}
        for network_id in KNOWN_NETWORKS.keys()
    ]

    return json_response({
        'networks': sorted(networks, key=lambda x: x['name'])
    })


@api.route('/settings/retention', methods=['GET'])
@log_request
def get_retention_settings():
    """Get retention configuration."""
    db = get_database()
    retention_days = int(db.get_setting('retention_days') or '30')
    return json_response({
        'retentionDays': retention_days,
        'enabled': retention_days > 0,
    })


@api.route('/settings/retention', methods=['PUT'])
@log_request
def update_retention_settings():
    """Update retention configuration."""
    data = request.get_json()
    if not data or 'retentionDays' not in data:
        return error_response('retentionDays is required', 400)

    days = data['retentionDays']
    if not isinstance(days, int) or days < 0 or days > 3650:
        return error_response('retentionDays must be an integer between 0 and 3650', 400)

    db = get_database()
    db.set_setting('retention_days', str(days), is_default=False)
    logger.info(f"Updated retention_days to {days}")

    return json_response({
        'retentionDays': days,
        'enabled': days > 0,
    })


@api.route('/settings/audio', methods=['GET'])
@log_request
def get_audio_settings():
    """Get audio-related settings (currently: keep original audio)."""
    db = get_database()
    raw = db.get_setting('keep_original_audio')
    keep = (raw or 'true').lower() != 'false'
    return json_response({'keepOriginalAudio': keep})


@api.route('/settings/audio', methods=['PUT'])
@log_request
def update_audio_settings():
    """Update audio-related settings."""
    data = request.get_json() or {}
    if 'keepOriginalAudio' not in data:
        return error_response('keepOriginalAudio is required', 400)
    keep = data['keepOriginalAudio']
    if not isinstance(keep, bool):
        return error_response('keepOriginalAudio must be a boolean', 400)
    db = get_database()
    db.set_setting('keep_original_audio', 'true' if keep else 'false', is_default=False)
    logger.info(f"Updated keep_original_audio to {keep}")
    return json_response({'keepOriginalAudio': keep})


@api.route('/settings/processing-timeouts', methods=['GET'])
@log_request
def get_processing_timeouts():
    """Get processing timeout configuration."""
    from processing_timeouts import (
        get_soft_timeout, get_hard_timeout,
        DEFAULT_SOFT_SECONDS, DEFAULT_HARD_SECONDS,
        SOFT_MIN, HARD_MAX,
    )
    return json_response({
        'softTimeoutSeconds': get_soft_timeout(),
        'hardTimeoutSeconds': get_hard_timeout(),
        'defaults': {
            'softTimeoutSeconds': DEFAULT_SOFT_SECONDS,
            'hardTimeoutSeconds': DEFAULT_HARD_SECONDS,
        },
        'limits': {
            'softMin': SOFT_MIN,
            'hardMax': HARD_MAX,
        },
    })


@api.route('/settings/processing-timeouts', methods=['PUT'])
@log_request
def update_processing_timeouts():
    """Update processing timeout configuration."""
    from processing_timeouts import validate, invalidate_cache
    data = request.get_json() or {}
    if 'softTimeoutSeconds' not in data or 'hardTimeoutSeconds' not in data:
        return error_response('softTimeoutSeconds and hardTimeoutSeconds are required', 400)

    soft = data['softTimeoutSeconds']
    hard = data['hardTimeoutSeconds']
    err = validate(soft, hard)
    if err:
        return error_response(err, 400)

    db = get_database()
    db.set_setting('processing_soft_timeout_seconds', str(soft), is_default=False)
    db.set_setting('processing_hard_timeout_seconds', str(hard), is_default=False)
    invalidate_cache()
    logger.info(f"Updated processing timeouts: soft={soft}s hard={hard}s")
    return json_response({
        'softTimeoutSeconds': soft,
        'hardTimeoutSeconds': hard,
    })


# ========== Webhook Helpers ==========

MAX_WEBHOOKS = 25


def _save_webhooks(db, webhooks):
    """Save webhooks list to DB settings."""
    db.set_setting('webhooks', json.dumps(webhooks), is_default=False)


def _strip_secret(webhook):
    """Return a copy of the webhook dict without the secret field."""
    return {k: v for k, v in webhook.items() if k != 'secret'}


def _find_webhook(webhooks, webhook_id):
    """Find a webhook by ID in the list. Returns the dict or None."""
    for wh in webhooks:
        if wh.get('id') == webhook_id:
            return wh
    return None


def _validate_events(events):
    """Validate events list. Returns error message string or None if valid."""
    if not events or not isinstance(events, list):
        return 'events must be a non-empty list'
    invalid = [e for e in events if e not in VALID_EVENTS]
    if invalid:
        return (f'Invalid events: {", ".join(invalid)}. '
                f'Valid events: {", ".join(sorted(VALID_EVENTS))}')
    return None


def _validate_webhook_url(url):
    """Validate a webhook URL. Returns error response or None if valid."""
    if not url:
        return error_response('url is required', 400)
    try:
        validate_url(url)
    except SSRFError as e:
        return error_response(f'Invalid webhook URL: {e}', 400)
    return None


# ========== Webhook Endpoints ==========

@api.route('/settings/webhooks', methods=['GET'])
@log_request
def list_webhooks():
    """List all webhooks, stripping secrets."""
    db = get_database()
    webhooks = load_webhooks(db)
    return json_response({'webhooks': [_strip_secret(wh) for wh in webhooks]})


@api.route('/settings/webhooks', methods=['POST'])
@log_request
def create_webhook():
    """Create a new webhook."""
    data = request.get_json()
    if not data:
        return error_response('Request body required', 400)

    url = data.get('url', '').strip()
    url_err = _validate_webhook_url(url)
    if url_err:
        return url_err

    events = data.get('events')
    events_err = _validate_events(events)
    if events_err:
        return error_response(events_err, 400)

    # Dry-render template if provided
    payload_template = data.get('payloadTemplate')
    if payload_template:
        try:
            render_template_preview(payload_template)
        except Exception as exc:
            return error_response(f'Invalid payloadTemplate: {exc}', 400)

    db = get_database()
    webhooks = load_webhooks(db)

    if len(webhooks) >= MAX_WEBHOOKS:
        return error_response(f'Maximum of {MAX_WEBHOOKS} webhooks allowed', 400)

    webhook = {
        'id': str(uuid.uuid4()),
        'url': url,
        'events': events,
        'secret': data.get('secret') or None,
        'enabled': data.get('enabled', True),
        'payloadTemplate': payload_template or None,
        'contentType': data.get('contentType', 'application/json'),
    }
    webhooks.append(webhook)
    _save_webhooks(db, webhooks)

    logger.info(f"Created webhook {webhook['id']} for {safe_url_for_log(url)}")
    return json_response(_strip_secret(webhook), status=201)


@api.route('/settings/webhooks/validate-template', methods=['POST'])
@log_request
@limiter.limit("30/minute")
def validate_webhook_template():
    """Validate and preview a webhook payload template."""
    data = request.get_json()
    if not data or 'template' not in data:
        return error_response('template is required', 400)

    try:
        preview = render_template_preview(data['template'])
        return json_response({
            'valid': True,
            'preview': preview,
            'error': None,
        })
    except Exception as exc:
        logger.warning("webhook template preview rendering failed: %s", exc)
        return json_response({
            'valid': False,
            'preview': '',
            'error': 'template rendering failed',
        })


@api.route('/settings/webhooks/<webhook_id>', methods=['PUT'])
@log_request
def update_webhook(webhook_id):
    """Update an existing webhook."""
    data = request.get_json()
    if not data:
        return error_response('Request body required', 400)

    db = get_database()
    webhooks = load_webhooks(db)
    target = _find_webhook(webhooks, webhook_id)
    if not target:
        return error_response('Webhook not found', 404)

    if 'url' in data:
        url = data['url'].strip()
        url_err = _validate_webhook_url(url)
        if url_err:
            return url_err
        target['url'] = url

    if 'events' in data:
        events_err = _validate_events(data['events'])
        if events_err:
            return error_response(events_err, 400)
        target['events'] = data['events']

    if 'enabled' in data:
        target['enabled'] = bool(data['enabled'])

    # Preserve existing secret if absent in body; normalize empty to None
    if 'secret' in data:
        target['secret'] = data['secret'] or None

    if 'contentType' in data:
        target['contentType'] = data['contentType']

    # If payloadTemplate is null or empty string, clear it
    if 'payloadTemplate' in data:
        template = data['payloadTemplate']
        if template is None or template == '':
            target['payloadTemplate'] = None
        else:
            try:
                render_template_preview(template)
            except Exception as exc:
                return error_response(f'Invalid payloadTemplate: {exc}', 400)
            target['payloadTemplate'] = template

    _save_webhooks(db, webhooks)
    logger.info(f"Updated webhook {webhook_id}")
    return json_response(_strip_secret(target))


@api.route('/settings/webhooks/<webhook_id>', methods=['DELETE'])
@log_request
def delete_webhook(webhook_id):
    """Delete a webhook."""
    db = get_database()
    webhooks = load_webhooks(db)

    original_len = len(webhooks)
    webhooks = [wh for wh in webhooks if wh.get('id') != webhook_id]

    if len(webhooks) == original_len:
        return error_response('Webhook not found', 404)

    _save_webhooks(db, webhooks)
    logger.info(f"Deleted webhook {webhook_id}")
    return json_response({'message': 'Webhook deleted'})


@api.route('/settings/webhooks/<webhook_id>/test', methods=['POST'])
@log_request
@limiter.limit("10/minute")
def test_webhook(webhook_id):
    """Send a test event to a webhook."""
    db = get_database()
    webhooks = load_webhooks(db)
    target = _find_webhook(webhooks, webhook_id)
    if not target:
        return error_response('Webhook not found', 404)

    try:
        success = fire_test_event(target)
        return json_response({
            'success': success,
            'message': 'Test webhook delivered' if success else 'Test webhook failed to deliver',
        })
    except Exception as e:
        logger.error(f"Webhook test failed for {webhook_id}: {e}")
        return json_response({
            'success': False,
            'message': 'webhook test failed; see server logs for details',
        })
