"""Settings routes: /settings/* endpoints."""
import json
import logging
import os
import uuid

from flask import request

from api import (
    api, log_request, json_response, error_response,
    get_database, _enrich_models_with_pricing, limiter,
)
from utils.url import validate_url, SSRFError
from webhook_service import render_template_preview, fire_test_event, load_webhooks, VALID_EVENTS

logger = logging.getLogger('podcast.api')


# ========== Settings Endpoints ==========

@api.route('/settings', methods=['GET'])
@log_request
def get_settings():
    """Get all settings."""
    db = get_database()
    from database import DEFAULT_SYSTEM_PROMPT, DEFAULT_VERIFICATION_PROMPT
    from ad_detector import AdDetector, DEFAULT_MODEL
    from chapters_generator import CHAPTERS_MODEL
    from llm_client import get_effective_provider, get_effective_base_url, get_api_key, PROVIDER_ANTHROPIC

    settings = db.get_all_settings()

    # Get current model settings
    current_model = settings.get('claude_model', {}).get('value', DEFAULT_MODEL)
    verification_model = settings.get('verification_model', {}).get('value', DEFAULT_MODEL)
    chapters_model = settings.get('chapters_model', {}).get('value', CHAPTERS_MODEL)

    # Get whisper model setting (defaults to env var or 'small')
    default_whisper_model = os.environ.get('WHISPER_MODEL', 'small')
    whisper_model = settings.get('whisper_model', {}).get('value', default_whisper_model)

    # Get auto-process setting (defaults to true)
    auto_process_value = settings.get('auto_process_enabled', {}).get('value', 'true')
    auto_process_enabled = auto_process_value.lower() in ('true', '1', 'yes')

    # Get Podcasting 2.0 settings (defaults to true)
    vtt_value = settings.get('vtt_transcripts_enabled', {}).get('value', 'true')
    vtt_enabled = vtt_value.lower() in ('true', '1', 'yes')
    chapters_value = settings.get('chapters_enabled', {}).get('value', 'true')
    chapters_enabled = chapters_value.lower() in ('true', '1', 'yes')

    # Get min cut confidence (ad detection aggressiveness)
    min_cut_confidence_str = settings.get('min_cut_confidence', {}).get('value', '0.80')
    try:
        min_cut_confidence = float(min_cut_confidence_str)
    except (ValueError, TypeError):
        min_cut_confidence = 0.80

    # LLM provider settings
    llm_provider = get_effective_provider()
    openai_base_url = get_effective_base_url()
    api_key = get_api_key()
    api_key_configured = bool(api_key and api_key != 'not-needed')

    return json_response({
        'systemPrompt': {
            'value': settings.get('system_prompt', {}).get('value', DEFAULT_SYSTEM_PROMPT),
            'isDefault': settings.get('system_prompt', {}).get('is_default', True)
        },
        'verificationPrompt': {
            'value': settings.get('verification_prompt', {}).get('value', DEFAULT_VERIFICATION_PROMPT),
            'isDefault': settings.get('verification_prompt', {}).get('is_default', True)
        },
        'claudeModel': {
            'value': current_model,
            'isDefault': settings.get('claude_model', {}).get('is_default', True)
        },
        'verificationModel': {
            'value': verification_model,
            'isDefault': settings.get('verification_model', {}).get('is_default', True)
        },
        'whisperModel': {
            'value': whisper_model,
            'isDefault': settings.get('whisper_model', {}).get('is_default', True)
        },
        'autoProcessEnabled': {
            'value': auto_process_enabled,
            'isDefault': settings.get('auto_process_enabled', {}).get('is_default', True)
        },
        'vttTranscriptsEnabled': {
            'value': vtt_enabled,
            'isDefault': settings.get('vtt_transcripts_enabled', {}).get('is_default', True)
        },
        'chaptersEnabled': {
            'value': chapters_enabled,
            'isDefault': settings.get('chapters_enabled', {}).get('is_default', True)
        },
        'chaptersModel': {
            'value': chapters_model,
            'isDefault': settings.get('chapters_model', {}).get('is_default', True)
        },
        'minCutConfidence': {
            'value': min_cut_confidence,
            'isDefault': settings.get('min_cut_confidence', {}).get('is_default', True)
        },
        'llmProvider': {
            'value': llm_provider,
            'isDefault': settings.get('llm_provider', {}).get('is_default', True)
        },
        'openaiBaseUrl': {
            'value': openai_base_url,
            'isDefault': settings.get('openai_base_url', {}).get('is_default', True)
        },
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
            'openaiBaseUrl': os.environ.get('OPENAI_BASE_URL', 'http://localhost:8000/v1')
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
        db.set_setting('llm_provider', data['llmProvider'], is_default=False)
        logger.info(f"Updated LLM provider to: {data['llmProvider']}")
        provider_changed = True

    if 'openaiBaseUrl' in data:
        from urllib.parse import urlparse
        parsed = urlparse(data['openaiBaseUrl'])
        if not parsed.scheme or parsed.scheme not in ('http', 'https') or not parsed.hostname:
            return json_response({'error': 'Invalid base URL: must be a valid http:// or https:// URL'}, 400)
        db.set_setting('openai_base_url', data['openaiBaseUrl'], is_default=False)
        logger.info(f"Updated OpenAI base URL to: {data['openaiBaseUrl']}")
        provider_changed = True

    if provider_changed:
        from llm_client import get_llm_client
        get_llm_client(force_new=True)

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
    from llm_client import get_llm_client
    db.reset_setting('llm_provider')
    db.reset_setting('openai_base_url')

    # Recreate LLM client with reset settings
    get_llm_client(force_new=True)

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
    """Get list of available Claude models."""
    from ad_detector import AdDetector

    ad_detector = AdDetector()
    models = ad_detector.get_available_models()
    _enrich_models_with_pricing(models)

    return json_response({'models': models})


@api.route('/settings/models/refresh', methods=['POST'])
@log_request
def refresh_models():
    """Force refresh the model list from the LLM provider."""
    from llm_client import get_llm_client
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

    logger.info(f"Created webhook {webhook['id']} for {url}")
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
        return json_response({
            'valid': False,
            'preview': '',
            'error': str(exc),
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
            'message': str(e),
        })
