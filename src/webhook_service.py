"""Outbound webhook dispatch with Jinja2 custom payload templates."""

import hashlib
import hmac
import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Optional

from jinja2 import TemplateError
from jinja2.sandbox import SandboxedEnvironment

from config import HTTP_MAX_REDIRECTS_API, HTTP_TIMEOUT_PROBE
from utils.http import safe_url_for_log
from utils.safe_http import URLTrust, safe_post
from utils.time import utc_now_iso
from utils.url import SSRFError

logger = logging.getLogger('podcast.webhooks')

EVENT_EPISODE_PROCESSED = 'Episode Processed'
EVENT_EPISODE_FAILED = 'Episode Failed'
EVENT_AUTH_FAILURE = 'Auth Failure'
VALID_EVENTS = {EVENT_EPISODE_PROCESSED, EVENT_EPISODE_FAILED, EVENT_AUTH_FAILURE}

_sandbox_env = SandboxedEnvironment()


@dataclass
class WebhookPayload:
    """Structured payload for webhook events."""
    event: str
    episode_id: str
    slug: str
    episode_title: str
    processing_time: Optional[float] = None
    llm_cost: Optional[float] = None
    ads_removed: int = 0
    error_message: Optional[str] = None
    original_duration: Optional[float] = None
    new_duration: Optional[float] = None
    podcast_name: Optional[str] = None


def _format_duration(seconds):
    """Format seconds as M:SS or H:MM:SS for webhook payloads."""
    if seconds is None:
        return None
    total = int(seconds)
    h, remainder = divmod(total, 3600)
    m, s = divmod(remainder, 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _format_cost(cost):
    """Format cost as $X.XX."""
    if cost is None:
        return None
    return f"${cost:.2f}"


_DUMMY_CONTEXT = {
    'event': 'Episode Processed',
    'timestamp': '',  # overwritten at render time with current UTC
    'podcast': {
        'name': 'Example Podcast',
        'slug': 'example-podcast',
    },
    'episode': {
        'id': 'abc123',
        'title': 'Example Episode Title',
        'slug': 'example-podcast',
        'url': 'http://your-server:8000/ui/feeds/example-podcast/episodes/abc123',
        'ads_removed': 3,
        'processing_time_secs': 42.5,
        'processing_time': _format_duration(42.5),
        'llm_cost': 0.0035,
        'llm_cost_display': _format_cost(0.0035),
        'time_saved_secs': 187.0,
        'time_saved': _format_duration(187.0),
        'error_message': None,
    },
}


def _build_context(payload: WebhookPayload) -> dict:
    """Build the template/payload context dict for a webhook event."""
    ui_base_url = os.environ.get('UI_BASE_URL') or os.environ.get('BASE_URL', 'http://localhost:8000')
    episode_url = f"{ui_base_url}/ui/feeds/{payload.slug}/episodes/{payload.episode_id}"

    if payload.original_duration is not None and payload.new_duration is not None:
        time_saved_secs = round(payload.original_duration - payload.new_duration, 2)
    else:
        time_saved_secs = None

    rounded_processing = round(payload.processing_time, 2) if payload.processing_time is not None else None
    rounded_cost = round(payload.llm_cost, 6) if payload.llm_cost is not None else None

    return {
        'event': payload.event,
        'timestamp': utc_now_iso(),
        'podcast': {
            'name': payload.podcast_name or payload.slug,
            'slug': payload.slug,
        },
        'episode': {
            'id': payload.episode_id,
            'title': payload.episode_title,
            'slug': payload.slug,
            'url': episode_url,
            'ads_removed': payload.ads_removed,
            'processing_time_secs': rounded_processing,
            'processing_time': _format_duration(rounded_processing),
            'llm_cost': rounded_cost,
            'llm_cost_display': _format_cost(rounded_cost),
            'time_saved_secs': time_saved_secs,
            'time_saved': _format_duration(time_saved_secs),
            'error_message': payload.error_message,
        },
    }


def _render_template(template_str, context):
    """Render a Jinja2 template in a sandboxed environment."""
    template = _sandbox_env.from_string(template_str)
    return template.render(**context)


def _prepare_and_dispatch(webhook_config, context, add_test_flag=False,
                          max_attempts=2):
    """Render payload and dispatch to a single webhook. Returns HTTP status or None."""
    url = webhook_config.get('url')
    if not url:
        return None

    if add_test_flag:
        context = dict(context)
        context['test'] = True

    content_type = webhook_config.get('contentType', 'application/json')
    template_str = webhook_config.get('payloadTemplate')

    if template_str:
        try:
            body_str = _render_template(template_str, context)
        except TemplateError as exc:
            logger.error("Jinja2 render error for webhook %s, skipping: %s", safe_url_for_log(url), exc)
            return None
    else:
        payload = dict(context)
        body_str = json.dumps(payload)

    body_bytes = body_str.encode('utf-8')

    headers = {'Content-Type': content_type}
    secret = webhook_config.get('secret')
    if secret:
        sig = hmac.new(
            secret.encode('utf-8'), body_bytes, hashlib.sha256
        ).hexdigest()
        headers['X-MinusPod-Signature'] = f"sha256={sig}"

    # Webhook URLs are operator-configured (admin-typed), so use
    # OPERATOR_CONFIGURED trust. safe_post revalidates every redirect hop
    # against the SSRF rules, which closes a gap where a legitimate webhook
    # host could 302 to a private IP. Retry loop wraps safe_post because
    # the consolidated fetcher is a single shot per call.
    last_status = None
    for attempt in range(max_attempts):
        try:
            resp = safe_post(
                url,
                trust=URLTrust.OPERATOR_CONFIGURED,
                timeout=HTTP_TIMEOUT_PROBE,
                max_redirects=HTTP_MAX_REDIRECTS_API,
                data=body_bytes,
                headers=headers,
            )
        except SSRFError as exc:
            logger.warning("Webhook URL blocked by SSRF check: %s (%s)", safe_url_for_log(url), exc)
            return None
        except Exception as exc:
            logger.warning(
                "Webhook attempt %d/%d failed for %s: %s",
                attempt + 1, max_attempts, url, exc,
            )
            continue

        last_status = resp.status_code
        if resp.status_code < 500:
            break

    if last_status is not None:
        logger.info("Webhook delivered to %s (status %d)", safe_url_for_log(url), last_status)
    return last_status


def load_webhooks(db=None):
    """Load webhooks list from DB settings."""
    if db is None:
        from database import Database  # deferred to avoid circular imports
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


def _fire_event_sync(payload: WebhookPayload):
    """Synchronous webhook dispatch -- called in a daemon thread by fire_event."""
    webhooks = load_webhooks()
    if not webhooks:
        return

    context = _build_context(payload)

    for wh in webhooks:
        if not wh.get('enabled', False):
            continue
        if payload.event not in wh.get('events', []):
            continue
        try:
            _prepare_and_dispatch(wh, context)
        except Exception:
            logger.exception("Unexpected error dispatching webhook to %s", wh.get('url'))


def fire_event(event, episode_id, slug, episode_title, processing_time,
               llm_cost, ads_removed=0, error_message=None,
               original_duration=None, new_duration=None,
               podcast_name=None):
    """Load webhooks from DB and dispatch to all matching subscribers.

    Dispatches in a daemon thread so the processing pipeline is never blocked.
    """
    if event not in VALID_EVENTS:
        logger.error("Invalid webhook event: %s", event)
        return

    payload = WebhookPayload(
        event=event,
        episode_id=episode_id,
        slug=slug,
        episode_title=episode_title,
        processing_time=processing_time,
        llm_cost=llm_cost,
        ads_removed=ads_removed,
        error_message=error_message,
        original_duration=original_duration,
        new_duration=new_duration,
        podcast_name=podcast_name,
    )

    thread = threading.Thread(
        target=_fire_event_sync,
        args=(payload,),
        daemon=True,
    )
    thread.start()


_auth_failure_lock = threading.Lock()
_last_auth_failure_time = 0.0
_AUTH_FAILURE_DEDUP_SECS = 300  # 5 minutes


def fire_auth_failure_event(provider, model, error_message, status_code):
    """Fire an auth failure webhook with 5-minute dedup to avoid spamming."""
    global _last_auth_failure_time

    now = time.time()
    with _auth_failure_lock:
        if now - _last_auth_failure_time < _AUTH_FAILURE_DEDUP_SECS:
            logger.debug("Auth failure webhook suppressed (dedup window)")
            return
        _last_auth_failure_time = now

    webhooks = load_webhooks()
    if not webhooks:
        return

    matching = [w for w in webhooks
                if w.get('enabled', False) and EVENT_AUTH_FAILURE in w.get('events', [])]
    if not matching:
        return

    context = {
        'event': EVENT_AUTH_FAILURE,
        'provider': provider,
        'model': model,
        'error_message': str(error_message),
        'status_code': status_code,
        'timestamp': utc_now_iso(),
    }

    def _dispatch():
        for wh in matching:
            try:
                _prepare_and_dispatch(wh, context)
                logger.info("Auth failure webhook sent (provider=%s, status=%s)",
                            provider, status_code)
            except Exception:
                logger.exception("Failed to send auth failure webhook to %s",
                                 wh.get('url'))

    thread = threading.Thread(target=_dispatch, daemon=True)
    thread.start()


def render_template_preview(template_string):
    """Render a Jinja2 template with dummy data for validation/preview.

    Returns the rendered string. Raises jinja2.TemplateError on invalid
    templates so callers can surface the error to the user.
    """
    context = dict(_DUMMY_CONTEXT)
    context['timestamp'] = utc_now_iso()
    return _render_template(template_string, context)


def fire_test_event(webhook_config):
    """Fire a test payload to a single webhook config dict.

    Attempts to load real data from the most recent completed
    processing_history entry. Falls back to synthetic placeholder data.

    Returns True on HTTP 2xx, False otherwise.
    """
    from database import Database  # deferred to avoid circular imports

    db = Database()
    context = None

    try:
        row = db.get_latest_completed_processing()
        if row:
            payload = WebhookPayload(
                event=EVENT_EPISODE_PROCESSED,
                episode_id=row['episode_id'],
                slug=row['podcast_slug'],
                episode_title=row['episode_title'],
                processing_time=row['processing_duration_seconds'],
                llm_cost=row['llm_cost'],
                ads_removed=row['ads_detected'],
                original_duration=row['original_duration'],
                new_duration=row['new_duration'],
                podcast_name=row.get('podcast_title'),
            )
            context = _build_context(payload)
    except Exception:
        logger.debug("Could not load real data for test webhook, using placeholders")

    if context is None:
        context = dict(_DUMMY_CONTEXT)
        context['timestamp'] = utc_now_iso()

    status = _prepare_and_dispatch(webhook_config, context, add_test_flag=True, max_attempts=1)
    if status is not None and 200 <= status < 300:
        return True
    return False
