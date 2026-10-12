"""Webhook and notification settings routes."""

import json
import logging
import sqlite3
import uuid
from flask import request
from api import (
    api, log_request, json_response, error_response,
    get_database, limiter,
)
from database.settings import SETTINGS_REGISTRY
from utils.url import (
    SSRFError, validate_base_url,
    validate_outbound_host,
)
from utils.http import safe_url_for_log
from utils.secret_writes import SecretWriteRejected, set_or_clear_secret
from webhook_service import (
    render_template_preview, fire_test_event, load_webhooks, VALID_EVENTS,
    get_notification_timezone,
)
import email_service
from email.utils import parseaddr
from api.settings import common

logger = logging.getLogger('podcast.api')


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
        validate_base_url(url)
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
        results = fire_test_event(target)
        delivered_count = sum(1 for r in results if r['delivered'])
        total = len(results)
        plural = '' if total == 1 else 's'
        message = f'{delivered_count} of {total} test payload{plural} delivered'
        fell_back = [r['event'] for r in results if r['templateFallback']]
        if fell_back:
            message += (
                f"; template could not render for {len(fell_back)} "
                f"event{'' if len(fell_back) == 1 else 's'} "
                f"(default payload used): {', '.join(fell_back)}"
            )
        return json_response({
            'success': delivered_count == total,
            'results': results,
            'message': message,
        })
    except Exception as e:
        logger.error(f"Webhook test failed for {webhook_id}: {e}")
        return json_response({
            'success': False,
            'results': [],
            'message': 'webhook test failed; see server logs for details',
        })


# ========== Email Notification settings ==========

def _email_address_invalid(addr: str) -> bool:
    """Reject addresses that fail a minimal name@host check, contain spaces,
    or carry CR/LF (header injection)."""
    if any(c in addr for c in '\r\n '):
        return True
    _, parsed = parseaddr(addr)
    return parsed != addr or '@' not in parsed.strip('@')


def _email_settings_response(db):
    cfg = email_service.load_email_config(db)
    return json_response({
        'enabled': cfg.enabled,
        'events': cfg.events,
        'smtpHost': cfg.host,
        'smtpPort': cfg.port,
        'smtpSecurity': cfg.security,
        'smtpUsername': cfg.username,
        'smtpPasswordConfigured': bool(db.get_setting('email_smtp_password')),
        'fromAddress': cfg.from_addr,
        'recipients': ', '.join(cfg.recipients),
    })


@api.route('/settings/notifications/email', methods=['GET'])
@log_request
def get_email_notification_settings():
    """Return the email notification settings (password never included)."""
    return _email_settings_response(get_database())


@api.route('/settings/notifications/email', methods=['PUT'])
@log_request
def update_email_notification_settings():
    """Update email notification settings.

    Partial body; smtpPassword is write-only (empty string clears it).
    Validation stages every field first and one transaction applies them, so
    neither a late 400 nor a failed write leaves part of the payload saved.
    """
    db = get_database()
    data = request.get_json() or {}

    staged = {}
    for field in ('smtpHost', 'smtpUsername', 'smtpPassword', 'fromAddress', 'recipients'):
        if field in data and not isinstance(data[field], (str, type(None))):
            return error_response(f'{field} must be a string', 400)
    if 'events' in data:
        events = data['events']
        if not isinstance(events, list) or any(e not in VALID_EVENTS for e in events):
            return error_response(
                f"events must be a list drawn from: {', '.join(sorted(VALID_EVENTS))}",
                400,
            )
        staged['email_events'] = json.dumps(events)
    if 'smtpPort' in data:
        port = data['smtpPort']
        if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
            return error_response('smtpPort must be an integer between 1 and 65535', 400)
        staged['email_smtp_port'] = str(port)
    if 'smtpSecurity' in data:
        security = data['smtpSecurity']
        if security not in email_service.VALID_SECURITY:
            return error_response(
                f"smtpSecurity must be one of: {', '.join(email_service.VALID_SECURITY)}",
                400,
            )
        staged['email_smtp_security'] = security
    if 'smtpHost' in data:
        host = (data['smtpHost'] or '').strip()
        if host:
            if any(c in host for c in '\r\n '):
                return error_response('smtpHost must not contain spaces or line breaks', 400)
            try:
                host = validate_outbound_host(host)
            except SSRFError as e:
                return error_response(str(e), 400)
        staged['email_smtp_host'] = host
    if 'smtpUsername' in data:
        username = (data['smtpUsername'] or '').strip()
        if '\r' in username or '\n' in username:
            return error_response('smtpUsername must not contain line breaks', 400)
        staged['email_smtp_username'] = username
    if 'fromAddress' in data:
        from_addr = (data['fromAddress'] or '').strip()
        if from_addr and _email_address_invalid(from_addr):
            return error_response('fromAddress is not a valid email address', 400)
        staged['email_smtp_from'] = from_addr
    if 'recipients' in data:
        raw = (data['recipients'] or '').strip()
        recipients = email_service.parse_recipients(raw)
        for addr in recipients:
            if _email_address_invalid(addr):
                return error_response(f'invalid recipient address: {addr}', 400)
        staged['email_recipients'] = ', '.join(recipients)
    if 'enabled' in data:
        enabled = bool(data['enabled'])
        if enabled:
            cfg = email_service.load_email_config(db)
            host = staged.get('email_smtp_host', cfg.host)
            from_addr = staged.get('email_smtp_from', cfg.from_addr)
            recipients = staged.get('email_recipients',
                                    ', '.join(cfg.recipients))
            if not (host and from_addr and recipients):
                return error_response(
                    'set an SMTP host, from address, and at least one '
                    'recipient before turning email notifications on',
                    400,
                )
        staged['email_enabled'] = 'true' if enabled else 'false'

    try:
        with db.settings_transaction():
            if 'smtpPassword' in data:
                try:
                    set_or_clear_secret(db, 'email_smtp_password', data['smtpPassword'])
                except SecretWriteRejected:
                    raise common._PhaseRejected(
                        error_response('provider_crypto_unavailable', 409)) from None
            for key, value in staged.items():
                db.set_setting(key, value, is_default=False)
    except common._PhaseRejected as rejected:
        return rejected.response
    except sqlite3.Error:
        logger.exception("Email settings save failed; no field was persisted")
        return error_response('Settings could not be saved', 500)

    if 'smtpPassword' in data:
        logger.info("Updated SMTP password")
    # Read back after the commit so the response reflects durable state.
    return _email_settings_response(db)


@api.route('/settings/notifications/email/test', methods=['POST'])
@log_request
@limiter.limit("10/minute")
def test_email_notifications():
    """Send a test email using the saved settings."""
    try:
        success, message = email_service.send_test_email(get_database())
        return json_response({'success': success, 'message': message})
    except Exception as e:
        logger.error(f"Email test failed: {e}")
        return json_response({
            'success': False,
            'message': 'email test failed; see server logs for details',
        })


# ========== Notification timezone ==========

@api.route('/settings/notifications/timezone', methods=['GET'])
@log_request
def get_notification_timezone_setting():
    """Return the IANA zone used for timestamp_local in webhook/email notifications."""
    return json_response({'timezone': get_notification_timezone(get_database())})


@api.route('/settings/notifications/timezone', methods=['PUT'])
@log_request
def update_notification_timezone_setting():
    """Set the notification timezone; rejects a name zoneinfo cannot resolve."""
    db = get_database()
    data = request.get_json() or {}
    tz = data.get('timezone')
    if not isinstance(tz, str) or not tz.strip():
        return error_response('timezone is required', 400)
    tz = tz.strip()
    if not SETTINGS_REGISTRY['notification_timezone'].validator(tz):
        return error_response(f'unknown timezone: {tz}', 400)
    db.set_setting('notification_timezone', tz, is_default=False)
    return json_response({'timezone': tz})
