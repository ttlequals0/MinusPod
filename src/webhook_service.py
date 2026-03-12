"""Outbound webhook dispatch with Jinja2 custom payload templates."""

import datetime
import hashlib
import hmac
import json
import logging
import os
import threading
import time
import urllib.request
import urllib.error

from jinja2 import TemplateError
from jinja2.sandbox import SandboxedEnvironment

logger = logging.getLogger('podcast.webhooks')

EVENT_EPISODE_PROCESSED = 'episode.processed'
EVENT_EPISODE_FAILED = 'episode.failed'
VALID_EVENTS = {EVENT_EPISODE_PROCESSED, EVENT_EPISODE_FAILED}

_RETRY_ATTEMPTS = 2
_RETRY_DELAY_SECS = 2
_REQUEST_TIMEOUT_SECS = 5

_sandbox_env = SandboxedEnvironment()

_DUMMY_CONTEXT = {
    'event': EVENT_EPISODE_PROCESSED,
    'timestamp': '2025-01-15T12:00:00Z',
    'episode': {
        'id': 'abc123',
        'title': 'Example Episode Title',
        'slug': 'example-podcast',
        'url': 'http://localhost:8000/ui/feeds/example-podcast/episodes/abc123',
        'ads_removed': 3,
        'processing_time_secs': 42.5,
        'llm_cost': 0.0035,
        'time_saved_secs': 187.0,
        'error_message': None,
    },
}


def _build_context(event, episode_id, slug, episode_title, processing_time,
                   llm_cost, ads_removed, error_message, original_duration,
                   new_duration):
    """Build the template/payload context dict for a webhook event."""
    base_url = os.environ.get('BASE_URL', 'http://localhost:8000')
    episode_url = f"{base_url}/ui/feeds/{slug}/episodes/{episode_id}"

    if original_duration is not None and new_duration is not None:
        time_saved_secs = original_duration - new_duration
    else:
        time_saved_secs = None

    return {
        'event': event,
        'timestamp': datetime.datetime.now(datetime.timezone.utc).strftime(
            '%Y-%m-%dT%H:%M:%SZ'
        ),
        'episode': {
            'id': episode_id,
            'title': episode_title,
            'slug': slug,
            'url': episode_url,
            'ads_removed': ads_removed,
            'processing_time_secs': processing_time,
            'llm_cost': llm_cost,
            'time_saved_secs': time_saved_secs,
            'error_message': error_message,
        },
    }


def _render_template(template_str, context):
    """Render a Jinja2 template in a sandboxed environment."""
    template = _sandbox_env.from_string(template_str)
    return template.render(**context)


def _dispatch_webhook(url, body_bytes, headers):
    """POST body_bytes to url with retry logic. Fire-and-forget."""
    for attempt in range(1, _RETRY_ATTEMPTS + 1):
        try:
            req = urllib.request.Request(
                url, data=body_bytes, headers=headers, method='POST'
            )
            with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT_SECS) as resp:
                logger.info(
                    "Webhook delivered to %s (attempt %d, status %d)",
                    url, attempt, resp.status,
                )
                return resp.status
        except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
            logger.warning(
                "Webhook delivery to %s failed (attempt %d/%d): %s",
                url, attempt, _RETRY_ATTEMPTS, exc,
            )
            if attempt < _RETRY_ATTEMPTS:
                time.sleep(_RETRY_DELAY_SECS)
        except Exception:
            logger.exception(
                "Unexpected error dispatching webhook to %s (attempt %d/%d)",
                url, attempt, _RETRY_ATTEMPTS,
            )
            if attempt < _RETRY_ATTEMPTS:
                time.sleep(_RETRY_DELAY_SECS)
    return None


def _prepare_and_dispatch(webhook_config, context, add_test_flag=False):
    """Render payload and dispatch to a single webhook. Returns HTTP status or None."""
    url = webhook_config.get('url')
    if not url:
        return None

    content_type = webhook_config.get('contentType', 'application/json')
    template_str = webhook_config.get('payloadTemplate')

    if template_str:
        try:
            body_str = _render_template(template_str, context)
        except TemplateError as exc:
            logger.error("Jinja2 render error for webhook %s, skipping: %s", url, exc)
            return None
    else:
        payload = dict(context)
        if add_test_flag:
            payload['test'] = True
        body_str = json.dumps(payload)

    body_bytes = body_str.encode('utf-8')

    headers = {'Content-Type': content_type}
    secret = webhook_config.get('secret')
    if secret:
        sig = hmac.new(
            secret.encode('utf-8'), body_bytes, hashlib.sha256
        ).hexdigest()
        headers['X-MinusPod-Signature'] = f"sha256={sig}"

    return _dispatch_webhook(url, body_bytes, headers)


def _load_webhooks():
    """Load webhooks list from DB settings."""
    from database import Database
    db = Database()
    raw = db.get_setting('webhooks')
    if not raw:
        return []
    try:
        webhooks = json.loads(raw)
        return webhooks if isinstance(webhooks, list) else []
    except (json.JSONDecodeError, TypeError):
        logger.error("Failed to parse webhooks setting from DB")
        return []


def _fire_event_sync(event, episode_id, slug, episode_title, processing_time,
                     llm_cost, ads_removed, error_message, original_duration,
                     new_duration):
    """Synchronous webhook dispatch -- called in a daemon thread by fire_event."""
    webhooks = _load_webhooks()
    if not webhooks:
        return

    context = _build_context(
        event, episode_id, slug, episode_title, processing_time,
        llm_cost, ads_removed, error_message, original_duration, new_duration,
    )

    for wh in webhooks:
        if not wh.get('enabled', False):
            continue
        if event not in wh.get('events', []):
            continue
        try:
            _prepare_and_dispatch(wh, context)
        except Exception:
            logger.exception("Unexpected error dispatching webhook to %s", wh.get('url'))


def fire_event(event, episode_id, slug, episode_title, processing_time,
               llm_cost, ads_removed=0, error_message=None,
               original_duration=None, new_duration=None):
    """Load webhooks from DB and dispatch to all matching subscribers.

    Dispatches in a daemon thread so the processing pipeline is never blocked.
    """
    if event not in VALID_EVENTS:
        logger.error("Invalid webhook event: %s", event)
        return

    thread = threading.Thread(
        target=_fire_event_sync,
        args=(event, episode_id, slug, episode_title, processing_time,
              llm_cost, ads_removed, error_message, original_duration,
              new_duration),
        daemon=True,
    )
    thread.start()


def render_template_preview(template_string):
    """Render a Jinja2 template with dummy data for validation/preview.

    Returns the rendered string. Raises jinja2.TemplateError on invalid
    templates so callers can surface the error to the user.
    """
    return _render_template(template_string, _DUMMY_CONTEXT)


def fire_test_event(webhook_config):
    """Fire a test payload to a single webhook config dict.

    Attempts to load real data from the most recent completed
    processing_history entry. Falls back to synthetic placeholder data.

    Returns True on HTTP 2xx, False otherwise.
    """
    from database import Database

    db = Database()
    context = None

    try:
        conn = db.get_connection()
        row = conn.execute(
            """SELECT episode_id, podcast_slug, episode_title,
                      processing_duration_seconds, llm_cost, ads_detected
               FROM processing_history
               WHERE status = 'completed'
               ORDER BY processed_at DESC
               LIMIT 1"""
        ).fetchone()
        if row:
            context = _build_context(
                event=EVENT_EPISODE_PROCESSED,
                episode_id=row[0],
                slug=row[1],
                episode_title=row[2],
                processing_time=row[3],
                llm_cost=row[4],
                ads_removed=row[5],
                error_message=None,
                original_duration=None,
                new_duration=None,
            )
    except Exception:
        logger.debug("Could not load real data for test webhook, using placeholders")

    if context is None:
        context = dict(_DUMMY_CONTEXT)

    status = _prepare_and_dispatch(webhook_config, context, add_test_flag=True)
    if status is not None and 200 <= status < 300:
        return True
    return False
